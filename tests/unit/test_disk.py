"""The disk guard, which stops a new download before the disk fills.

The upload ceiling says how large a file Telegram will accept. It says nothing about how
much room is left. A 1.9 GB file on a disk with 200 MB free is accepted by the Bot API and
rejected by the filesystem, and by then the damage is done: SQLite cannot write, the local
Bot API server cannot write its media, and the container may not come back up.

The rule is deliberately about free space rather than a percentage used, because "full" is
the failure that cannot be recovered from and a percentage has no idea how big the disk is.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from bot.domain.errors import DiskSpaceError
from bot.services.disk import DiskGuard, FreeSpace

GIGA = 1_000_000_000


@dataclass(frozen=True, slots=True)
class Reported:
    """A measurement, as ``shutil.disk_usage`` would report one."""

    free: int


@dataclass
class FakeUsage:
    """Stands in for ``shutil.disk_usage``, so free space is a number in a test.

    Not a mock: the point of these tests is the comparison, and a real filesystem's free
    space would make every assertion depend on which disk the suite happens to run on.
    """

    free_bytes: int = 100 * GIGA
    calls: int = 0

    def __call__(self, _path: Path) -> Reported:
        self.calls += 1
        return Reported(free=self.free_bytes)


def a_guard(
    tmp_path: Path,
    *,
    threshold_gb: float = 5.0,
    usage: object | None = None,
) -> DiskGuard:
    return DiskGuard(
        tmp_path,
        threshold_gb=threshold_gb,
        usage=usage if usage is not None else FakeUsage(),  # type: ignore[arg-type]
    )


# -- The threshold ----------------------------------------------------------- #


async def test_plenty_of_room_means_the_download_may_start(tmp_path: Path) -> None:
    assert await a_guard(tmp_path, usage=FakeUsage(free_bytes=50 * GIGA)).has_room()


async def test_a_disk_below_the_threshold_stops_the_download(tmp_path: Path) -> None:
    assert not await a_guard(tmp_path, usage=FakeUsage(free_bytes=2 * GIGA)).has_room()


async def test_the_threshold_is_the_line_and_a_free_byte_above_it_passes(tmp_path: Path) -> None:
    # Exactly at the threshold is refused, and so is a hair under. One byte of headroom is
    # not enough room for a file of any useful size, so the boundary belongs to the refusal.
    exactly = a_guard(tmp_path, usage=FakeUsage(free_bytes=5 * GIGA))
    just_above = a_guard(tmp_path, usage=FakeUsage(free_bytes=5 * GIGA + 1))

    assert not await exactly.has_room()
    assert await just_above.has_room()


async def test_a_threshold_of_zero_refuses_only_a_disk_with_no_room_at_all(tmp_path: Path) -> None:
    # Zero is a supported setting, so it has to mean something honest. It removes the
    # reserve, not the guard: a volume with one byte free can still take nothing, and
    # refusing there is the whole point of the guard.
    one_byte_free = a_guard(tmp_path, threshold_gb=0.0, usage=FakeUsage(free_bytes=1))
    full = a_guard(tmp_path, threshold_gb=0.0, usage=FakeUsage(free_bytes=0))

    assert await one_byte_free.has_room()
    assert not await full.has_room()


async def test_the_free_space_of_the_volume_is_measured_not_the_directory_size(
    tmp_path: Path,
) -> None:
    # The directory is where downloads land, but the number that matters belongs to the
    # filesystem it sits on.
    usage = FakeUsage(free_bytes=50 * GIGA)
    guard = a_guard(tmp_path, usage=usage)

    assert await guard.has_room()
    assert usage.calls == 1


# -- What the user is told ---------------------------------------------------- #


async def test_the_refusal_names_the_reason_and_says_when_to_try_again(tmp_path: Path) -> None:
    # "Something went wrong" is not an answer. The user is told the bot is out of room and
    # that trying again is the thing to do, because there is nothing they can fix.
    guard = a_guard(tmp_path, usage=FakeUsage(free_bytes=1 * GIGA))

    error = await guard.check()

    assert isinstance(error, DiskSpaceError)
    said = str(error)
    assert "disk" in said.lower()
    assert "later" in said.lower()


async def test_the_refusal_reports_the_free_space_in_the_units_it_is_configured_in(
    tmp_path: Path,
) -> None:
    # Decimal, because DISK_FREE_THRESHOLD_GB is decimal and mixing the two would produce a
    # message that says 1.1 GB is under a 5 GB limit on a disk that plainly has less.
    guard = a_guard(tmp_path, usage=FakeUsage(free_bytes=1_100_000_000))

    error = await guard.check()

    assert isinstance(error, DiskSpaceError)
    assert "1.1 GB" in str(error)
    assert "5.0 GB" in str(error)


async def test_a_download_may_start_when_there_is_room(tmp_path: Path) -> None:
    assert await a_guard(tmp_path, usage=FakeUsage(free_bytes=50 * GIGA)).check() is None


# -- Failures ---------------------------------------------------------------- #


async def test_a_disk_that_cannot_be_measured_lets_the_download_try(
    tmp_path: Path,
) -> None:
    # Refusing every download because a stat failed would look exactly like the guard
    # working. A missing volume is somebody else's problem to fix, and yt-dlp's own error
    # is a better message than a guess.
    def explode(_path: Path) -> FreeSpace:
        raise OSError("the volume is not mounted")

    assert await a_guard(tmp_path, usage=explode).has_room()


async def test_a_measurement_failure_raises_nothing(tmp_path: Path) -> None:
    def explode(_path: Path) -> FreeSpace:
        raise OSError("the volume is not mounted")

    assert await a_guard(tmp_path, usage=explode).check() is None


# -- Not blocking the loop ---------------------------------------------------- #


async def test_the_measurement_runs_off_the_event_loop(tmp_path: Path) -> None:
    # ``disk_usage`` is a syscall. On a network volume it can block, and blocking it inline
    # stalls every update in flight behind it.
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    background = asyncio.create_task(ticker())
    try:
        await a_guard(tmp_path).has_room()
    finally:
        background.cancel()

    assert ticks > 0
