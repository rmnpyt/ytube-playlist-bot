"""The menu: greeting, help, and the buttons that start a flow.

Everything here is short. The bot's whole job is to get out of the way of the work, and
a menu that explains itself at length is a menu nobody reads.

Access is checked by :func:`requires_access` rather than in each handler. A check that
has to be remembered is a check that will be forgotten in the one handler that matters.
"""

from __future__ import annotations

import re
from functools import wraps
from typing import TYPE_CHECKING

from telegram.ext import CommandHandler, MessageHandler, filters

from bot.container import services_from
from bot.keyboards.reply import (
    ADMIN_LABEL,
    HELP_LABEL,
    PLAYLIST_LABEL,
    SINGLE_VIDEO_LABEL,
    main_menu_keyboard,
)
from bot.services.users import Sighting

if TYPE_CHECKING:
    from telegram import Update

    from bot.container import BotApplication, BotContext
    from bot.handlers import Handler

HELP_TEXT = (
    "Send me a YouTube link and I'll show you the qualities I can send back. "
    "Pick one and it arrives here as a video, or as a file if your player would "
    "rather not cope.\n\n"
    "You can cancel a download at any time while it is running."
)

NOT_YET_TEXT = "That part isn't finished yet. Single video and Help work today."

DENIED_TEXT = "This bot is by invitation. Ask one of its admins for access."


def requires_access(handler: Handler) -> Handler:
    """Refuse the request unless this user is allowed to use the bot."""

    @wraps(handler)
    async def guarded(update: Update, context: BotContext) -> None:
        services = services_from(context)
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        # Recorded first, and unconditionally. An admin needs to be able to see who is
        # knocking, and somebody who was just refused is the person most worth seeing.
        await services.users.record_sighting(Sighting(user.id, user.username))
        if not await services.users.may_use(user.id):
            await message.reply_text(DENIED_TEXT)
            return
        await handler(update, context)

    return guarded


async def _menu_reply(update: Update, context: BotContext, text: str) -> None:
    services = services_from(context)
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    await message.reply_text(
        text,
        reply_markup=main_menu_keyboard(is_admin=services.settings.is_admin(user.id)),
    )


@requires_access
async def start(update: Update, context: BotContext) -> None:
    await _menu_reply(update, context, "What would you like to do?")


@requires_access
async def help_command(update: Update, context: BotContext) -> None:
    await _menu_reply(update, context, HELP_TEXT)


@requires_access
async def on_single_video(update: Update, context: BotContext) -> None:
    services = services_from(context)
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return
    services.flow.expect_link(chat.id)
    await message.reply_text("Send me a YouTube link.")


@requires_access
async def on_playlist(update: Update, context: BotContext) -> None:
    await _menu_reply(update, context, NOT_YET_TEXT)


@requires_access
async def on_admin(update: Update, context: BotContext) -> None:
    services = services_from(context)
    user = update.effective_user
    if user is None or not services.settings.is_admin(user.id):
        # The button is not shown to non-admins, but the label is just text on the wire
        # and anybody can send it.
        await _menu_reply(update, context, DENIED_TEXT)
        return
    await _menu_reply(update, context, NOT_YET_TEXT)


@requires_access
async def on_unknown_text(update: Update, context: BotContext) -> None:
    await show_unknown_text(update, context)


async def show_unknown_text(update: Update, context: BotContext) -> None:
    """The catch-all for text that is not a menu tap and not a link.

    Undecorated so the single-video handler can fall through to it without asking twice
    whether the user is allowed in.
    """
    await _menu_reply(update, context, "Tap one of the buttons, or send a link.")


def register_menu(
    application: BotApplication,
) -> None:
    """Commands and menu buttons.

    Registered first, in the default group: a tap on "Single video" is text too, and the
    single-video handler must not get there first.
    """

    def tapped(label: str) -> filters.BaseFilter:
        # A tap arrives as the whole message. An unescaped emoji or a stray "." would
        # otherwise widen the match to messages nobody tapped.
        return filters.Regex(rf"^{re.escape(label)}$")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(tapped(SINGLE_VIDEO_LABEL), on_single_video))
    application.add_handler(MessageHandler(tapped(PLAYLIST_LABEL), on_playlist))
    application.add_handler(MessageHandler(tapped(HELP_LABEL), help_command))
    application.add_handler(MessageHandler(tapped(ADMIN_LABEL), on_admin))


def register_fallback(
    application: BotApplication,
) -> None:
    """The text catch-all.

    Registered last and in the default group, so the single-video handler gets first
    refusal on every message that is not a command.
    """
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_unknown_text))
