"""Tests for the playlist service protocol and its fake."""

from __future__ import annotations

from bot.domain.protocols import PlaylistService


class FakePlaylist(PlaylistService):
    """In-memory playlist for tests."""

    def __init__(self) -> None:
        self._queue: dict[int, list[tuple[str, str]]] = {}
        self._running: set[tuple[str, str]] = set()
        self._done: set[tuple[str, str]] = set()
        self._failed: set[tuple[str, str]] = set()

    async def queue(self, chat_id: int, url: str, quality: str) -> None:
        self._queue.setdefault(chat_id, []).append((url, quality))

    async def next_pending(self, chat_id: int) -> tuple[str, str] | None:
        pending = self._queue.get(chat_id, [])
        for item in pending:
            if item not in self._running and item not in self._done:
                # failed items are NOT excluded — they should be retried on restart
                return item
        return None

    async def mark_running(self, url: str, quality: str) -> None:
        self._running.add((url, quality))

    async def mark_done(self, url: str, quality: str) -> None:
        self._done.add((url, quality))

    async def mark_failed(self, url: str, quality: str) -> None:
        self._failed.add((url, quality))


async def test_fake_playlist_stores_and_returns_pending() -> None:
    p = FakePlaylist()
    await p.queue(1, "https://youtu.be/a", "137")
    await p.queue(1, "https://youtu.be/b", "22")

    item = await p.next_pending(1)
    assert item == ("https://youtu.be/a", "137")

    item = await p.next_pending(1)
    assert item == ("https://youtu.be/a", "137")  # still pending until marked running


async def test_fake_playlist_skips_running_and_done() -> None:
    p = FakePlaylist()
    await p.queue(1, "https://youtu.be/a", "137")
    await p.queue(1, "https://youtu.be/b", "22")

    await p.mark_running("https://youtu.be/a", "137")
    item = await p.next_pending(1)
    assert item == ("https://youtu.be/b", "22")

    await p.mark_done("https://youtu.be/a", "137")
    item = await p.next_pending(1)
    assert item == ("https://youtu.be/b", "22")  # still pending


async def test_fake_playlist_resumes_failed_on_restart() -> None:
    """A job marked failed stays pending so a restart retries it."""
    p = FakePlaylist()
    await p.queue(1, "https://youtu.be/a", "137")

    await p.mark_failed("https://youtu.be/a", "137")
    item = await p.next_pending(1)
    # The fake returns failed items as pending — the real impl will filter them
    # This test documents the fake's current behaviour; the real impl test will differ
    assert item == ("https://youtu.be/a", "137")
