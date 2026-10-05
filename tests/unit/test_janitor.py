"""The janitor, which deletes downloads nobody finished with.

The download path removes its own file on every way out, so the janitor only ever sees
what a hard kill left behind: the process died between writing the file and deleting it,
so nothing in the running bot knows the file is there. These tests write real files in a
real directory, because the behaviour under test is a filesystem walk, and a mock of that
walk would only prove the mock works.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from bot.services.janitor import Janitor, SweepResult

if TYPE_CHECKING:
    from collections.abc import Callable

#: The clock the tests pretend it is. Old enough that ageing a file against real time
#: would do, and fixed so a file's age is exactly what the test said it is.
NOW = time.time()
TTL = 3600.0
INTERVAL = 900.0


@dataclass
class FakeSleep:
    """Stands in for ``asyncio.sleep`` so the loop can be stepped instead of waited on.

    Ends the loop by raising what a real shutdown raises, so ``run`` is exercised through
    the same path a Ctrl-C or a container stop takes.
    """

    requested: list[float] = field(default_factory=list)
    before_sleep: list[Callable[[], None]] = field(default_factory=list)
    after: int = 3

    async def __call__(self, seconds: float) -> None:
        self.requested.append(seconds)
        index = len(self.requested)
        if index <= len(self.before_sleep):
            self.before_sleep[index - 1]()
        if index >= self.after:
            raise asyncio.CancelledError


def an_aged_file(directory: Path, name: str, age_seconds: float) -> Path:
    """A file that looks ``age_seconds`` old to the janitor."""
    path = directory / name
    path.write_bytes(b"video")
    stamp = NOW - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def a_janitor(directory: Path, **changes: Any) -> Janitor:
    return Janitor(
        directory, ttl_seconds=TTL, interval_seconds=INTERVAL, clock=lambda: NOW, **changes
    )


async def a_sweep(directory: Path, **changes: Any) -> SweepResult:
    return await a_janitor(directory, **changes).sweep()


# -- What gets removed ------------------------------------------------------ #


async def test_a_file_older_than_the_ttl_is_removed(tmp_path: Path) -> None:
    stale = an_aged_file(tmp_path, "A Video.mp4", TTL + 1)

    result = await a_sweep(tmp_path)

    assert result.removed == 1
    assert not stale.exists()


async def test_a_file_inside_the_ttl_is_kept(tmp_path: Path) -> None:
    # The distinction the janitor exists for. A file that arrived a second ago is somebody's
    # download in progress, and deleting it would break a transfer that is working.
    fresh = an_aged_file(tmp_path, "A Video.mp4", TTL - 1)

    result = await a_sweep(tmp_path)

    assert result.removed == 0
    assert fresh.exists()


async def test_a_file_exactly_at_the_ttl_is_kept(tmp_path: Path) -> None:
    # The boundary belongs to the young side: a sweep must not race a file that is merely
    # old enough, because old enough and abandoned are not the same moment.
    boundary = an_aged_file(tmp_path, "A Video.mp4", TTL)

    result = await a_sweep(tmp_path)

    assert result.removed == 0
    assert boundary.exists()


async def test_only_the_stale_files_go(tmp_path: Path) -> None:
    stale = an_aged_file(tmp_path, "stale.mp4", TTL + 60)
    fresh = an_aged_file(tmp_path, "fresh.mp4", 1)

    result = await a_sweep(tmp_path)

    assert result.removed == 1
    assert not stale.exists()
    assert fresh.exists()


async def test_a_partial_download_is_swept_like_any_other_file(tmp_path: Path) -> None:
    # yt-dlp leaves ``.part`` files behind when it is killed. Nothing on disk marks them as
    # different, so age is the only signal they get, and it is enough.
    partial = an_aged_file(tmp_path, "A Video.mp4.part", TTL + 1)

    result = await a_sweep(tmp_path)

    assert result.removed == 1
    assert not partial.exists()


# -- What it leaves alone ---------------------------------------------------- #


async def test_directories_are_never_removed(tmp_path: Path) -> None:
    # Removing a directory would take a tree with it. The janitor removes files and stops.
    nested = tmp_path / "an old directory"
    nested.mkdir()
    an_aged_file(nested, "inside.mp4", TTL + 1)
    os.utime(nested, (NOW - TTL - 60, NOW - TTL - 60))

    result = await a_sweep(tmp_path)

    assert result.removed == 0
    assert nested.is_dir()
    assert (nested / "inside.mp4").exists()


async def test_a_symlink_is_not_followed_out_of_the_download_directory(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    precious = tmp_path / "precious.db"
    precious.write_bytes(b"not a video")
    os.utime(precious, (NOW - TTL - 1, NOW - TTL - 1))
    link = downloads / "innocent.mp4"
    try:
        link.symlink_to(precious)
    except OSError:
        pytest.skip("this platform does not allow symlinks in the test environment")

    result = await a_sweep(downloads)

    assert result.removed == 0
    assert precious.exists()
    assert link.is_symlink()


async def test_a_missing_directory_is_an_empty_sweep_not_a_failure(tmp_path: Path) -> None:
    # The first start after a deploy may find no download directory at all, and a janitor
    # that refused to start over a missing folder would take the whole bot down with it.
    result = await a_sweep(tmp_path / "never created")

    assert result.removed == 0
    assert result.freed_bytes == 0


# -- What it reports --------------------------------------------------------- #


async def test_a_sweep_reports_what_it_reclaimed(tmp_path: Path) -> None:
    an_aged_file(tmp_path, "one.mp4", TTL + 1)
    an_aged_file(tmp_path, "two.mp4", TTL + 1)

    result = await a_sweep(tmp_path)

    assert result.removed == 2
    assert result.freed_bytes == 10


# -- Failures ---------------------------------------------------------------- #


async def test_one_unremovable_file_does_not_stop_the_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A file still held open by a process that has not gone away is what a slow teardown
    # looks like. Letting it end the sweep would leave more behind, not fewer.
    busy = an_aged_file(tmp_path, "busy.mp4", TTL + 1)
    later = an_aged_file(tmp_path, "later.mp4", TTL + 1)
    original = Path.unlink

    def refuse(self: Path, missing_ok: bool = False) -> None:
        if self.name == "busy.mp4":
            raise PermissionError("the file is open elsewhere")
        original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)

    result = await a_sweep(tmp_path)

    assert result.removed == 1
    assert not later.exists()
    assert busy.exists()


# -- The loop ---------------------------------------------------------------- #


async def test_the_loop_waits_before_its_first_sweep(tmp_path: Path) -> None:
    # Who sweeps first is the caller's decision, not the loop's. The entrypoint sweeps once
    # before it serves anybody, because a restart's leftovers should be gone before the first
    # download is measured against the free space on disk.
    stale = an_aged_file(tmp_path, "left over.mp4", TTL + 1)
    sleep = FakeSleep(after=1)

    with pytest.raises(asyncio.CancelledError):
        await a_janitor(tmp_path, sleep=sleep).run()

    assert stale.exists()
    assert sleep.requested == [INTERVAL]


async def test_the_loop_sweeps_again_after_every_interval(tmp_path: Path) -> None:
    # A janitor that swept once and stopped would be indistinguishable from no janitor at
    # all. The file planted during the first wait can only be removed by a later sweep.
    def plant() -> None:
        an_aged_file(tmp_path, "second.mp4", TTL + 1)

    sleep = FakeSleep(before_sleep=[plant], after=2)

    with pytest.raises(asyncio.CancelledError):
        await a_janitor(tmp_path, sleep=sleep).run()

    assert not (tmp_path / "second.mp4").exists()
    assert sleep.requested == [INTERVAL, INTERVAL]


async def test_a_sweep_that_explodes_does_not_end_the_janitor(tmp_path: Path) -> None:
    # The janitor is a background task. An exception nobody caught would end it silently and
    # no sweep would ever run again, which is the exact failure this phase exists to stop.
    stale = an_aged_file(tmp_path, "stale.mp4", TTL + 1)

    class Flaky(Janitor):
        def __init__(self, directory: Path, **kwargs: Any) -> None:
            super().__init__(directory, **kwargs)
            self.attempts = 0

        async def sweep(self) -> SweepResult:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("the sweep fell over")
            return await super().sweep()

    flaky = Flaky(tmp_path, ttl_seconds=TTL, clock=lambda: NOW, sleep=FakeSleep(after=3))

    with pytest.raises(asyncio.CancelledError):
        await flaky.run()

    assert flaky.attempts == 2
    assert not stale.exists()


async def test_the_loop_keeps_going_on_the_interval(tmp_path: Path) -> None:
    sweeps: list[int] = []

    class Counting(Janitor):
        async def sweep(self) -> SweepResult:
            sweeps.append(1)
            return await super().sweep()

    sleep = FakeSleep()
    counting = Counting(tmp_path, ttl_seconds=TTL, clock=lambda: NOW, sleep=sleep)

    with pytest.raises(asyncio.CancelledError):
        await counting.run()

    assert len(sweeps) == 2
    assert sleep.requested == [INTERVAL, INTERVAL, INTERVAL]


# -- Not blocking the loop ---------------------------------------------------- #


async def test_the_sweep_runs_off_the_event_loop(tmp_path: Path) -> None:
    # Walking a directory of large files is blocking work. Done inline it would stall every
    # update in flight for as long as it took.
    an_aged_file(tmp_path, "stale.mp4", TTL + 1)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    background = asyncio.create_task(ticker())
    try:
        await a_sweep(tmp_path)
    finally:
        background.cancel()

    assert ticks > 0
