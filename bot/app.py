"""Assembly: the one place the bot is put together.

Handlers are written against a container and an application alias, and they are tested
against fakes. Something has to build the real thing, and this is it, so that the wiring
is in one readable place instead of spread through whichever module happened to need the
database first.

Two orderings matter here and both are easy to get wrong silently:

* ``menu``, then ``single``, then the menu's text catch-all. The catch-all matches every
  text message, so registering it before the single-video handler turns every link into
  "tap one of the buttons" and no test that does not drive a real update will notice.
* Container before handlers. A handler registered first and called before the container
  is in ``bot_data`` would raise a ``KeyError`` on the first update of a fresh deploy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder

from bot.container import SERVICES_KEY, Services
from bot.handlers import menu, single
from bot.logging_setup import get_logger
from bot.services.files import FileDelivery
from bot.services.flow import Flow
from bot.services.users import UserService
from bot.services.youtube.service import YtDlpService

if TYPE_CHECKING:
    from bot.config import Settings
    from bot.container import BotApplication, BotContext
    from bot.services.storage.db import Database

LOGGER_NAME = "bot.app"

#: What a user is told when a failure the bot cannot explain reaches the last handler.
#:
#: Deliberately not the exception text. A stack trace in a chat names files, queries and
#: code, none of which is the sender's business and all of which is useful to whoever
#: reads the log instead.
GENERIC_APOLOGY = "Sorry, something went wrong on my side. Nothing was changed."


def build_application(settings: Settings, database: Database) -> BotApplication:
    """Build the bot: the API client, the container, the handlers, the error handler.

    Nothing here touches the network. ``initialize`` does that, and the entrypoint calls
    it through ``start``, so a test can build and drive the same application a container
    runs without a Telegram connection anywhere.
    """
    application = cast(
        "BotApplication",
        ApplicationBuilder()
        .token(settings.telegram_bot_token.get_secret_value())
        # PTB appends the token itself, so the configured URL is the prefix. This is the
        # local Bot API server, and local mode is what makes getFile return a path on the
        # server's disk instead of handing the bot a file to pull back over the wire.
        .base_url(settings.telegram_bot_api_url)
        .local_mode(settings.telegram_bot_api_local)
        .build(),
    )
    application.bot_data[SERVICES_KEY] = build_services(application, settings, database)
    application.add_error_handler(on_application_error)

    menu.register_menu(application)
    single.register(application)
    menu.register_fallback(application)
    return application


def build_services(application: BotApplication, settings: Settings, database: Database) -> Services:
    """Assemble the container the handlers are allowed to reach for.

    The application is an argument rather than something built here because delivery
    needs the bot to send through, and the bot is what the application owns. One of the
    two has to come second; passing the application in keeps that fact in the signature
    instead of hiding it behind a global.
    """
    return Services(
        settings=settings,
        users=UserService(
            database.sessions,
            # Admins are named in the environment and have no database row, so the two
            # lists are unioned here. Reading only the configured allowlist would lock
            # every admin out of the bot that only admins can fix.
            allowed_ids=settings.admin_user_ids | settings.allowed_user_ids,
        ),
        videos=YtDlpService(settings),
        delivery=FileDelivery(application.bot),
        flow=Flow(),
    )


async def on_application_error(update: object, context: BotContext) -> None:
    """The backstop for anything a handler did not catch.

    Two audiences, deliberately given different things. The log gets the exception and
    everything known about the update, because that is the only copy anyone will have.
    The user gets one sentence, because a traceback pasted into a chat is noise at best
    and a disclosure at worst.

    The error arrives on the context, not as an argument: this is the signature
    python-telegram-bot calls, and it hands over a context built from the failure.

    Logged through structlog rather than the standard library on purpose: an HTTP error
    from the Bot API carries the request URL, and that URL carries the bot token. The
    redaction processor scrubs it; a bare ``logging.exception`` would not.
    """
    _log_failure(update, context.error)
    await _tell_the_user(update)


def _log_failure(update: object, error: Exception | None) -> None:
    """Record the cause where the operator will find it, at a level that fits it."""
    log = get_logger(LOGGER_NAME)
    known: dict[str, object] = {
        "update_id": getattr(update, "update_id", None),
        "error_type": type(error).__name__ if error is not None else None,
    }
    if error is None:
        # Not a path the library takes: it builds the context from the exception. Handled
        # anyway, because the alternative is a handler that raises while reporting a
        # failure, which turns one broken update into a loop of them.
        log.warning("an update failed and the error was not attached to it", **known)
        return
    if isinstance(error, TelegramError):
        # Telegram refusing a request is not a bug: usually a flood limit or a message
        # that no longer exists. A traceback for it would bury the failures that are.
        log.warning("telegram refused a request", detail=str(error), **known)
        return
    # The exception is handed over rather than left to be picked up from the ambient
    # exception state, because this runs from a task rather than from inside an
    # ``except`` block, where that state is already gone.
    log.exception(  # noqa: LOG004 - reporting a failure from outside an ``except`` block
        "unhandled error while dispatching an update", exc_info=error, **known
    )


async def _tell_the_user(update: object) -> None:
    """Say something to whoever triggered the failure, if there is anyone to say it to."""
    query = getattr(update, "callback_query", None)
    if query is not None:
        # Answered either way, so the client stops spinning the button. The user is not
        # told what went wrong: they pressed a button that is now stale, which the next
        # message from them will fix.
        try:
            await query.answer()
        except TelegramError:
            _log_reply_failure("could not answer a callback query after a failure")
        return

    message = getattr(update, "effective_message", None)
    if message is None:
        # An inline query, a reaction, a poll answer. Nothing to reply to, which is why
        # the log line in _log_failure is the important half of this function.
        return
    try:
        await message.reply_text(GENERIC_APOLOGY)
    except TelegramError:
        _log_reply_failure("could not apologise after a failure")


def _log_reply_failure(detail: str) -> None:
    """The apology itself failed, which is worth knowing and worth saying once."""
    get_logger(LOGGER_NAME).warning("could not reach the user after a failure", detail=detail)
