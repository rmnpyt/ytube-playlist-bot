"""Sending a finished download, and getting rid of it afterwards.

Two things live here that are easy to get wrong in a handler. Telegram rejects a
caption over 1024 characters, and video titles are routinely longer than that. And the
file on disk is worthless once it has been sent, so it is removed on every path out,
including the one where the user pressed Cancel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Telegram's own limit, not one of ours.
MAX_CAPTION_LENGTH = 1024


class SupportsSending(Protocol):
    """The part of the bot this module uses.

    Narrowed to two methods so the delivery rules can be tested without a Telegram
    connection, and so a future change to the library's surface shows up here first.
    """

    async def send_video(
        self,
        chat_id: int,
        video: Path,
        caption: str | None = None,
    ) -> object: ...

    async def send_document(
        self,
        chat_id: int,
        document: Path,
        caption: str | None = None,
    ) -> object: ...


def fit_caption(text: str) -> str:
    """Trim a caption to what Telegram will accept.

    The video title is user data, so the cut is explicit rather than silent: a caption
    that ends in an ellipsis reads as truncated, which is honest, where a caption that
    silently stops mid-word reads as corruption.
    """
    if len(text) <= MAX_CAPTION_LENGTH:
        return text
    return text[: MAX_CAPTION_LENGTH - 1].rstrip() + "…"


class FileDelivery:
    """Hands a downloaded file to Telegram, then cleans up after itself."""

    def __init__(self, bot: SupportsSending) -> None:
        self._bot = bot

    async def deliver(self, chat_id: int, path: Path, *, as_video: bool) -> None:
        """Send the file the way this user asked to receive it."""
        caption = fit_caption(path.stem)
        if as_video:
            await self._bot.send_video(chat_id, path, caption=caption)
        else:
            await self._bot.send_document(chat_id, path, caption=caption)

    async def discard(self, path: Path) -> bool:
        """Delete a downloaded file. Whether the file is gone afterwards.

        A cancelled download may never have created the file, which counts as success:
        the caller wants the path to be free, not a report on what was there.
        """
        return await asyncio.to_thread(self._remove, path)

    @staticmethod
    def _remove(path: Path) -> bool:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove %s", path, exc_info=True)
            return False
        return True
