"""Stand-ins for the services a handler reaches for.

Fakes rather than mocks, because a test should read as a sentence about behaviour:
``FakeVideos.succeeded`` says more than ``videos.download_video.return_value``.

The user and delivery fakes subclass their real classes. Those two are concrete classes
rather than protocols, so a duck-typed stand-in would have to be cast into the type
anyway; inheriting gets the signatures checked by mypy for free. The YouTube fake needs
no such help, because ``YoutubeService`` is a protocol and a plain class already
satisfies it.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from bot.domain.errors import DiskSpaceError, DownloadCancelledError


class FakeDatabase:
    """A database stand-in for tests that don't need real persistence."""

    def __init__(self) -> None:
        self._disposed = False

    @property
    def sessions(self):
        """Return None - tests don't use real sessions."""
        return None

    async def dispose(self) -> None:
        self._disposed = True

    @property
    def disposed(self) -> bool:
        return self._disposed


from bot.domain.models import (
    DownloadProgress,
    QualityMenu,
    QualityOption,
    VideoDescription,
    VideoMetadata,
)
from bot.domain.protocols import DiskRoom, PlaylistService
from bot.services.disk import NO_ROOM
from bot.services.files import FileDelivery
from bot.services.users import Sighting, User, UserService

if TYPE_CHECKING:
    from bot.domain.protocols import ProgressCallback


class FakeUsers(UserService):
    """A user service that answers from memory."""

    def __init__(self, *, allowed: bool = True, send_as_video: bool = True) -> None:
        self.allowed = allowed
        self.send_as_video = send_as_video
        self.sightings: list[Sighting] = []
        self._allowed_overrides: dict[int, bool] = {}
        self._pending_requests: dict[int, bool] = {}

    async def record_sighting(self, sighting: Sighting) -> None:
        self.sightings.append(sighting)

    async def may_use(self, telegram_id: int) -> bool:
        if telegram_id in self._allowed_overrides:
            return self._allowed_overrides[telegram_id]
        return self.allowed

    async def prefers_video(self, telegram_id: int) -> bool:
        return self.send_as_video

    async def request_access(self, telegram_id: int, username: str | None) -> bool:
        # Users who are already allowed (globally or by override) cannot request access
        if await self.may_use(telegram_id):
            return False
        if telegram_id in self._pending_requests:
            return False
        self._pending_requests[telegram_id] = True
        return True

    async def list_pending_requests(self) -> list[User]:
        from bot.services.users import User

        return [
            User(telegram_id=tid, username=None, send_as_video=True, is_allowed=False)
            for tid in self._pending_requests
        ]

    async def approve_request(self, telegram_id: int) -> bool:
        if telegram_id not in self._pending_requests:
            return False
        self._allowed_overrides[telegram_id] = True
        del self._pending_requests[telegram_id]
        return True

    async def deny_request(self, telegram_id: int) -> bool:
        if telegram_id not in self._pending_requests:
            return False
        del self._pending_requests[telegram_id]
        return True

    async def set_allowed(self, telegram_id: int, is_allowed: bool) -> None:
        self._allowed_overrides[telegram_id] = is_allowed

    async def list_users(self) -> list[User]:
        from bot.services.users import User

        return [
            User(
                telegram_id=tid,
                username=None,
                send_as_video=True,
                is_allowed=self._allowed_overrides.get(tid, self.allowed),
            )
            for tid in set(self._allowed_overrides) | set(self._pending_requests)
        ]


class FakeVideos:
    """A YouTube service that answers with whatever the test told it to."""

    def __init__(
        self,
        description: VideoDescription | None = None,
        *,
        error: Exception | None = None,
        download_error: Exception | None = None,
        contents: str = "video bytes",
        cancel_during: bool = False,
    ) -> None:
        self.description = description
        self.error = error
        self.download_error = download_error
        self.contents = contents
        self.cancel_during = cancel_during
        self.described: list[str] = []
        self.downloads: list[tuple[str, str]] = []
        self.was_cancelled: bool = False

    async def describe_video(self, url: str) -> VideoDescription:
        self.described.append(url)
        if self.error is not None:
            raise self.error
        if self.description is None:
            raise AssertionError("the test did not supply a description to return")
        return self.description

    async def download_video(
        self,
        url: str,
        format_id: str,
        *,
        on_progress: ProgressCallback | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> Path:
        self.downloads.append((url, format_id))
        if on_progress is not None:
            await on_progress(self._snapshot())
        if self.cancel_during and cancelled is not None:
            # What the real adapter does when the user presses Cancel: raise the flag, and
            # notice it on the next progress tick.
            cancelled.set()
        if cancelled is not None and cancelled.is_set():
            self.was_cancelled = True
            raise DownloadCancelledError
        if self.download_error is not None:
            raise self.download_error
        return self.write_file()

    def _snapshot(self) -> DownloadProgress:
        return DownloadProgress(
            downloaded_bytes=len(self.contents.encode()),
            total_bytes=len(self.contents.encode()),
            speed_bytes_per_second=1024.0,
            eta_seconds=1,
        )

    def write_file(self) -> Path:
        """Put a file where a real download would have left one."""
        path = Path(tempfile.mkdtemp()) / "A Video.mp4"
        path.write_text(self.contents)
        return path


class FakeDelivery(FileDelivery):
    """A delivery service that records instead of sending, and still cleans up."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.delivered: list[tuple[int, str, bool]] = []
        self.discarded: list[str] = []

    async def deliver(self, chat_id: int, path: Path, *, as_video: bool) -> None:
        if self.error is not None:
            raise self.error
        self.delivered.append((chat_id, path.name, as_video))

    async def discard(self, path: Path) -> bool:
        self.discarded.append(path.name)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        return True


class FakeDisk(DiskRoom):
    """A disk with as much room as the test says it has.

    Defaults to room, because a test that is not about the disk should not have to think
    about it, and one that is should say so by passing ``room=False``.
    """

    def __init__(self, *, room: bool = True) -> None:
        self.room = room
        self.asked = 0

    async def check(self) -> DiskSpaceError | None:
        self.asked += 1
        if self.room:
            return None
        return DiskSpaceError(NO_ROOM)


class FakePlaylist(PlaylistService):
    """In-memory playlist for tests."""

    def __init__(self) -> None:
        self._queue: dict[int, list[tuple[str, str]]] = {}
        self._running: set[tuple[str, str]] = set()
        self._done: set[tuple[str, str]] = set()

    async def queue(self, chat_id: int, url: str, quality: str) -> None:
        self._queue.setdefault(chat_id, []).append((url, quality))

    async def next_pending(self, chat_id: int) -> tuple[str, str] | None:
        pending = self._queue.get(chat_id, [])
        for item in pending:
            if item not in self._running and item not in self._done:
                return item
        return None

    async def mark_running(self, url: str, quality: str) -> None:
        self._running.add((url, quality))

    async def mark_done(self, url: str, quality: str) -> None:
        self._done.add((url, quality))

    async def mark_failed(self, url: str, quality: str) -> None:
        # Failed items are NOT added to a set; they stay pending for retry
        pass


def a_description(
    *,
    title: str = "A Video",
    video_id: str = "abcdefghijk",
    options: tuple[QualityOption, ...] | None = None,
    removed: tuple[QualityMenu.Removed, ...] = (),
    limit_bytes: int = 0,
) -> VideoDescription:
    """Metadata plus one offerable quality, which is the smallest menu worth testing."""
    return VideoDescription(
        metadata=VideoMetadata(video_id=video_id, title=title, duration_seconds=212),
        menu=QualityMenu(
            options=options if options is not None else (an_option(),),
            removed=removed,
            limit_bytes=limit_bytes,
        ),
    )


def an_option(
    format_id: str = "137", height: int = 1080, size_bytes: int = 12_000_000
) -> QualityOption:
    return QualityOption(format_id=format_id, height=height, size_bytes=size_bytes)
