"""The single-video flow: link in, quality out, file delivered.

Three updates drive it. A link arrives as text, a quality arrives as a button press, and
Cancel arrives as another button press while the transfer runs. None of them need a
conversation state machine, because a chat is only ever in one of three states, and
those live in :class:`~bot.services.flow.Flow`.

Routing order matters here. The menu's text catch-all would swallow every link if it
were registered first, so the application wires the modules in this order: menu buttons,
then this, then the catch-all.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Message
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from bot.container import services_from
from bot.domain.errors import BotError, DownloadCancelledError, InternalError
from bot.handlers.menu import requires_access, show_unknown_text
from bot.keyboards.inline import (
    CANCEL_CALLBACK,
    QUALITY_PREFIX,
    cancel_keyboard,
    quality_keyboard,
    quality_menu_text,
)
from bot.services.flow import PendingVideo
from bot.services.progress import ProgressReporter

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path
    from typing import Any

    from telegram import InlineKeyboardMarkup, MaybeInaccessibleMessage, Update

    from bot.container import BotApplication, BotContext, Services
    from bot.domain.models import DownloadProgress, QualityOption

    #: Something a callback query's message can be. Telegram may hand back a message
    #: that no longer exists, which is exactly the case where an edit must be skipped.
    Editable = MaybeInaccessibleMessage

logger = logging.getLogger(__name__)

EXPIRED_MENU = "That menu has expired. Send the link again."
CANCELLED = "Cancelled."
STOPPING = "Stopping…"
NOTHING_TO_CANCEL = "There is no download to cancel."
DONE = "Done."
INTERRUPTED = "Something went wrong and the download stopped. Try again."
DELIVERY_FAILED = "The file downloaded but could not be sent. Please try again."


@requires_access
async def on_text(update: Update, context: BotContext) -> None:
    """Take a link if one was asked for, otherwise hand the user back to the menu."""
    services = services_from(context)
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return

    if not services.flow.expecting_link(chat.id):
        await show_unknown_text(update, context)
        return

    text = (message.text or "").strip()
    try:
        description = await services.videos.describe_video(text)
    except BotError as error:
        # The user pasted something unusable. The flow stays open so they can try again
        # without going back through the menu.
        await message.reply_text(str(error))
        return

    services.flow.stop_expecting_link(chat.id)
    services.flow.remember(
        PendingVideo(chat_id=chat.id, url=text, description=description),
    )
    await message.reply_text(
        quality_menu_text(description),
        reply_markup=quality_keyboard(description),
    )


@requires_access
async def on_quality_choice(update: Update, context: BotContext) -> None:
    """Start the download the chosen button asked for."""
    services = services_from(context)
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    if query is None or query.data is None or chat is None or user is None:
        return
    await query.answer()

    pending = services.flow.recall(chat.id)
    option = _chosen_option(pending, query.data)
    if pending is None or option is None:
        # Either the menu expired, or somebody forged a button for a format that was
        # never offered. Both get the same answer rather than being treated as a bug.
        await _say(query.message, EXPIRED_MENU)
        return

    await _say(query.message, f"Starting {option.label}…", keyboard=cancel_keyboard())
    # The menu is spent before the download starts, not after it ends. A second press on
    # the same button would otherwise start a second transfer for the same file, and both
    # would clean up through one shared state entry.
    services.flow.forget(chat.id)
    await _download(
        services,
        pending=pending,
        user_id=user.id,
        option=option,
        message=query.message,
    )


@requires_access
async def on_cancel(update: Update, context: BotContext) -> None:
    """Ask a running download to stop."""
    services = services_from(context)
    query = update.callback_query
    chat = update.effective_chat
    if query is None or chat is None:
        return
    await query.answer()

    if services.flow.cancel(chat.id):
        # Not "Cancelled" yet: yt-dlp has to notice and unwind before the file is
        # removed, so claiming otherwise would promise something not yet true.
        await _say(query.message, STOPPING)
        return
    await _say(query.message, NOTHING_TO_CANCEL)


def _chosen_option(pending: PendingVideo | None, data: str) -> QualityOption | None:
    """The format the button asked for, if it is one we actually offered."""
    if pending is None:
        return None
    wanted = data.removeprefix(QUALITY_PREFIX)
    return next((option for option in pending.menu.options if option.format_id == wanted), None)


async def _download(
    services: Services,
    *,
    pending: PendingVideo,
    user_id: int,
    option: QualityOption,
    message: Editable | None,
) -> None:
    """Fetch one format, send it, and remove it whatever happens."""
    cancel = services.flow.begin(pending.chat_id)
    reporter = ProgressReporter(_edit_line(message))
    path = None

    try:
        path = await services.videos.download_video(
            pending.url,
            option.format_id,
            on_progress=_ignore_result(reporter.report),
            cancelled=cancel,
        )
    except DownloadCancelledError:
        await _say(message, CANCELLED)
    except BotError as error:
        await _report_failure(message, error)
    except Exception:
        logger.exception("download failed unexpectedly")
        await _say(message, INTERRUPTED)
    else:
        await _send(
            services,
            chat_id=pending.chat_id,
            user_id=user_id,
            path=path,
            message=message,
        )
    finally:
        services.flow.end(pending.chat_id)
        if path is not None:
            # After the outcome has been reported, so a file that cannot be removed is
            # a leftover the janitor can see rather than one nobody hears about.
            await services.delivery.discard(path)


async def _send(
    services: Services,
    *,
    chat_id: int,
    user_id: int,
    path: Path,
    message: Editable | None,
) -> None:
    """Hand the downloaded file over, honouring how this user wants it."""
    try:
        as_video = await services.users.prefers_video(user_id)
        await services.delivery.deliver(chat_id, path, as_video=as_video)
    except BotError:
        logger.warning("delivery rejected for chat %s", chat_id, exc_info=True)
        await _say(message, DELIVERY_FAILED)
    except Exception:
        logger.exception("delivery failed unexpectedly")
        await _say(message, DELIVERY_FAILED)
    else:
        await _say(message, DONE)


async def _report_failure(message: Editable | None, error: BotError) -> None:
    if isinstance(error, InternalError):
        # The detail belongs in the log; a traceback in the chat helps nobody. The
        # exception object is all there is to log here, since this is a plain function
        # rather than the except block that caught it.
        logger.warning("download failed: %r", error)
        await _say(message, INTERRUPTED)
        return
    await _say(message, str(error))


async def _say(
    message: Editable | None,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Replace a message's text, if there is an editable message to replace."""
    if not isinstance(message, Message):
        return
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except Exception:
        # A message that cannot be edited is no reason to fail the work behind it.
        logger.debug("could not edit message", exc_info=True)


def _edit_line(message: Editable | None) -> Callable[[str], Coroutine[Any, Any, None]]:
    """An editor for the progress reporter, or a no-op when there is no message.

    This deliberately does not go through :func:`_say`. The reporter treats a refused
    edit as information, and a helper that swallowed every failure would report every
    edit as a success.
    """

    async def edit(line: str) -> None:
        if not isinstance(message, Message):
            return
        await message.edit_text(line)

    return edit


def _ignore_result(
    report: Callable[[DownloadProgress], Coroutine[Any, Any, bool]],
) -> Callable[[DownloadProgress], Coroutine[Any, Any, None]]:
    """Adapt the reporter, which says whether it edited, to the service's callback."""

    async def relay(progress: DownloadProgress) -> None:
        await report(progress)

    return relay


def register(
    application: BotApplication,
) -> None:
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(CallbackQueryHandler(on_quality_choice, pattern=rf"^{QUALITY_PREFIX}"))
    application.add_handler(CallbackQueryHandler(on_cancel, pattern=rf"^{CANCEL_CALLBACK}$"))
