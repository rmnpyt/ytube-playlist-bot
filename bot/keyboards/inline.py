"""Inline keyboards attached to the messages the bot sends while working.

Callback data is kept short and namespaced because Telegram caps it at 64 bytes: the
quality buttons carry only the format id, which is all the handler needs to start a
download, and the cancel button is a single fixed word.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from bot.domain.models import VideoDescription

QUALITY_PREFIX = "quality:"
CANCEL_CALLBACK = "cancel"


def quality_keyboard(description: VideoDescription) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(option.label, callback_data=f"{QUALITY_PREFIX}{option.format_id}")
        for option in description.menu.options
    ]
    return InlineKeyboardMarkup([[button] for button in buttons])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✖ Cancel", callback_data=CANCEL_CALLBACK)]],
    )


def quality_menu_text(description: VideoDescription) -> str:
    """The message the quality buttons sit under."""
    lines = [description.metadata.caption, "", "Pick a quality:"]
    note = description.menu.removal_note
    if note:
        lines.extend(("", note))
    return "\n".join(lines)
