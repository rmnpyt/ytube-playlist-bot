"""What each chat is in the middle of, and how to stop it.

State lives in memory and is deliberately not in the database. A chat that the bot
forgets about on restart has merely lost a menu the user can summon again; a
half-finished download that outlives the process is a different problem, and one the
container being restarted has already solved by dying.

Cancellation is cooperative. The service takes an ``asyncio.Event`` and checks it from
yt-dlp's progress hook, which is what actually stops a transfer mid-flight. Cancelling
the task instead would unwind the coroutine from the outside and leave the file cleanup
running through a cancelled frame, which is a much worse thing to reason about.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.domain.models import QualityOption, VideoDescription


@dataclass(frozen=True, slots=True)
class PendingDownload:
    """A video the user has been shown and not yet chosen from."""

    chat_id: int
    description: VideoDescription
    option: QualityOption


class Flow:
    """Per-chat state: what is being asked for, and what is running."""

    def __init__(self) -> None:
        self._awaiting_link: set[int] = set()
        self._pending: dict[int, PendingDownload] = {}
        self._cancels: dict[int, asyncio.Event] = {}

    # -- Awaiting a link --------------------------------------------------- #

    def expect_link(self, chat_id: int) -> None:
        """The user asked for a video and owes us a URL."""
        self._awaiting_link.add(chat_id)

    def expecting_link(self, chat_id: int) -> bool:
        return chat_id in self._awaiting_link

    def stop_expecting_link(self, chat_id: int) -> None:
        self._awaiting_link.discard(chat_id)

    # -- A video waiting for a choice -------------------------------------- #

    def remember(self, pending: PendingDownload) -> None:
        self._pending[pending.chat_id] = pending

    def recall(self, chat_id: int) -> PendingDownload | None:
        return self._pending.get(chat_id)

    def forget(self, chat_id: int) -> None:
        self._pending.pop(chat_id, None)
        self._cancels.pop(chat_id, None)

    # -- A running download ------------------------------------------------ #

    def begin(self, chat_id: int) -> asyncio.Event:
        """Register a download in progress and return the flag that stops it."""
        cancel = asyncio.Event()
        self._cancels[chat_id] = cancel
        return cancel

    def end(self, chat_id: int) -> None:
        self._cancels.pop(chat_id, None)

    def cancel(self, chat_id: int) -> bool:
        """Ask the running download to stop. Whether there was one."""
        cancel = self._cancels.get(chat_id)
        if cancel is None or cancel.is_set():
            return False
        cancel.set()
        return True

    def is_running(self, chat_id: int) -> bool:
        cancel = self._cancels.get(chat_id)
        return cancel is not None and not cancel.is_set()
