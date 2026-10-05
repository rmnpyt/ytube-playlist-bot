"""Collecting the downloads that nobody finished with.

The download path deletes its own file on every way out, so nothing here is ever needed
while the bot behaves. It exists for the way the bot stops behaving: a container killed
mid-transfer, a host reboot, an OOM. In those cases the ``finally`` that would have
deleted the file never runs, the file is still there, and nothing in the running process
knows about it. Left alone it stays there until the disk is full, which on a VPS takes
the database and the local Bot API server down with it.

So the rule is deliberately blunt: a regular file under the download directory that has
not been touched for longer than the configured age is removed. Young files belong to a
transfer in progress. A download that runs for longer than the age is a bug worth having
anyway, and it is bounded by the upload ceiling rather than by time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SweepResult:
    """What one pass over the download directory found."""

    removed: int
    freed_bytes: int


class Sweeper(Protocol):
    """The janitor as the entrypoint uses it, and nothing more.

    Narrowed to the two calls ``serve`` makes, so the entrypoint can be tested against a
    stand-in and a change to the janitor's own surface shows up here first.
    """

    async def sweep(self) -> SweepResult: ...

    async def run(self) -> None: ...


class Janitor:
    """Removes stale files, on a schedule, for as long as the process runs."""

    def __init__(
        self,
        directory: Path,
        *,
        ttl_seconds: float,
        interval_seconds: float = 900.0,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """
        ``clock`` and ``sleep`` exist so the loop can be tested by stepping it rather than
        by waiting for it. Ageing a file in a test is a matter of setting its timestamp
        against a fixed clock, which is the only way to test a boundary without a sleep.
        """
        self._directory = directory
        self._ttl = ttl_seconds
        self._interval = interval_seconds
        self._clock = clock if clock is not None else time.time
        self._sleep = sleep if sleep is not None else asyncio.sleep

    @property
    def directory(self) -> Path:
        """The directory this janitor sweeps."""
        return self._directory

    @property
    def ttl_seconds(self) -> float:
        """How old a file has to be before it is treated as abandoned."""
        return self._ttl

    @property
    def interval_seconds(self) -> float:
        """How long the loop waits between sweeps."""
        return self._interval

    async def sweep(self) -> SweepResult:
        """Remove everything stale, and report what that freed."""
        return await asyncio.to_thread(self._sweep, self._clock())

    async def run(self) -> None:
        """Sweep on the interval, forever, until the process is stopped.

        Sleeps before the first sweep on purpose: the entrypoint runs one pass itself,
        before it starts serving, so the space a crash left behind is reclaimed before
        anything is measured against it.
        """
        while True:
            await self._sleep(self._interval)
            await self._sweep_safely()

    async def _sweep_safely(self) -> None:
        """One sweep that cannot end the loop, whatever goes wrong inside it.

        A background task that raises once is gone, silently, and no sweep would ever run
        again. That failure is indistinguishable from never having built the janitor, so it
        is logged and the loop carries on to the next tick.
        """
        try:
            result = await self.sweep()
        except Exception:
            logger.warning("janitor sweep failed", exc_info=True)
            return
        if result.removed:
            logger.info(
                "janitor removed %d stale file(s), freeing %d byte(s)",
                result.removed,
                result.freed_bytes,
            )

    def _sweep(self, now: float) -> SweepResult:
        removed = 0
        freed = 0
        for entry in self._entries():
            try:
                size = self._reclaim(entry, now)
            except OSError:
                # One file that cannot be removed is a file somebody else still has open.
                # Ending the sweep over it would leave everything after it behind too.
                logger.warning("could not remove %s", entry, exc_info=True)
                continue
            if size is not None:
                removed += 1
                freed += size
        return SweepResult(removed, freed)

    def _entries(self) -> list[Path]:
        try:
            return list(self._directory.iterdir())
        except OSError:
            # A first start with no download directory yet is an empty sweep. Treating it as
            # a failure would stop the bot over an empty folder.
            logger.info("nothing to sweep: %s is not there", self._directory)
            return []

    def _reclaim(self, entry: Path, now: float) -> int | None:
        """Delete one stale file. Its size if it went, otherwise ``None``.

        ``None`` rather than ``0`` because "left alone" and "an empty file went" are
        different answers and only one of them is worth counting.
        """
        if entry.is_symlink() or not entry.is_file():
            # A symlink is not followed out of the directory: unlinking one removes the link
            # and leaves whatever it points at alone, but only because it is skipped first.
            return None
        stats = entry.stat()
        if now - stats.st_mtime <= self._ttl:
            return None
        entry.unlink()
        return stats.st_size
