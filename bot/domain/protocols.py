"""The interfaces handlers are allowed to depend on.

This module exists so the seam is declared once, in a place with no dependencies of its
own. Handlers take a ``YoutubeService``; production passes the yt-dlp adapter and tests
pass a fake. That is what makes the single-video flow testable without a network, and it
is the only reason this rewrite is tractable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Coroutine
    from pathlib import Path
    from typing import Any

    from bot.domain.errors import DiskSpaceError
    from bot.domain.models import DownloadProgress, VideoDescription

    # A coroutine, not a bare Awaitable: the adapter hands these to
    # run_coroutine_threadsafe, which needs something the loop can wrap.
    ProgressCallback = Callable[[DownloadProgress], Coroutine[Any, Any, None]]


@runtime_checkable
class DiskRoom(Protocol):
    """Whether the download directory can take one more file.

    ``check`` rather than ``has_room`` at the call site, because the caller needs the reason
    to show the user and a bare ``False`` cannot be printed.
    """

    async def check(self) -> DiskSpaceError | None:
        """The refusal to raise, or ``None`` when there is room.

        Returns rather than raises: a full disk is an expected state, not an exception, and
        the handler decides how to say so. Never raises for a filesystem that cannot be
        measured.
        """


@runtime_checkable
class PlaylistService(Protocol):
    """Persist and replay the playlist selections a user makes.

    The handler only knows it can queue a URL with a chosen quality and ask for the next
    pending job. The concrete implementation uses the database; tests pass a fake.
    """

    async def queue(self, chat_id: int, url: str, quality: str) -> None:
        """Store one playlist entry, pending."""

    async def next_pending(self, chat_id: int) -> tuple[str, str] | None:
        """The next pending (url, quality) for this chat, or ``None`` if the queue is empty."""

    async def mark_running(self, url: str, quality: str) -> None:
        """The download has started; if the process dies here, restart will retry."""

    async def mark_done(self, url: str, quality: str) -> None:
        """The download finished and the file was delivered."""

    async def mark_failed(self, url: str, quality: str) -> None:
        """The download failed; restart will retry."""


@runtime_checkable
class YoutubeService(Protocol):
    """Everything the bot needs from YouTube."""

    async def describe_video(self, url: str) -> VideoDescription:
        """Read metadata and build the quality menu, without downloading anything.

        Raises:
            VideoUnavailableError: YouTube will not describe the video.
            AllOptionsTooLargeError: every quality exceeds the upload ceiling.
            NoDownloadableOptionsError: there is nothing that can be sent.
        """

    async def download_video(
        self,
        url: str,
        format_id: str,
        *,
        on_progress: ProgressCallback | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> Path:
        """Fetch one format to a local file.

        Raises:
            DownloadCancelledError: ``cancelled`` was set while the transfer ran.
            DownloadFailedError: yt-dlp or ffmpeg failed.
        """
