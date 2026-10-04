"""Specification for progress throttling.

The clock is injected throughout. A test that sleeps for two seconds to prove a
throttle works is a test nobody runs.
"""

from __future__ import annotations

import pytest
from telegram.error import TelegramError

from bot.domain.models import DownloadProgress
from bot.services.progress import ProgressReporter, ProgressThrottle


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    """Stands in for editing a Telegram message."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    async def __call__(self, line: str) -> None:
        self.lines.append(line)


def progress(downloaded: int) -> DownloadProgress:
    return DownloadProgress(
        downloaded_bytes=downloaded,
        total_bytes=100_000_000,
        speed_bytes_per_second=1_000_000.0,
        eta_seconds=10,
    )


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([0.0, 0.1, 0.2, 2.0], [True, False, False, True]),
        ([0.0, 1.9], [True, False]),
        ([0.0, 2.0], [True, True]),
        ([5.0, 5.0], [True, False]),
        ([0.0, 2.0, 2.0], [True, True, False]),
    ],
)
def test_only_one_event_per_interval_gets_through(
    events: list[float], expected: list[bool]
) -> None:
    throttle = ProgressThrottle(2.0)

    assert [throttle.allows(now) for now in events] == expected


def test_the_first_event_is_never_withheld() -> None:
    # A user who has just tapped a button should see something happen at once.
    assert ProgressThrottle(60.0).allows(0.0) is True


async def test_frequent_updates_produce_few_edits() -> None:
    # yt-dlp calls back several times a second; the message must not follow it there.
    clock, recorder = Clock(), Recorder()
    reporter = ProgressReporter(recorder, clock=clock)

    for step in range(20):
        clock.now = step * 0.25
        await reporter.report(progress(step * 10_000_000))

    assert len(recorder.lines) == 3


async def test_a_throttled_update_is_reported_as_not_shown() -> None:
    clock, recorder = Clock(), Recorder()
    reporter = ProgressReporter(recorder, clock=clock)

    assert await reporter.report(progress(0)) is True
    clock.now = 0.5
    assert await reporter.report(progress(500)) is False
    assert len(recorder.lines) == 1


async def test_the_final_line_lands_even_inside_the_throttle_window() -> None:
    # The last line carries exact byte counts, so it must not be swallowed.
    clock, recorder = Clock(), Recorder()
    reporter = ProgressReporter(recorder, clock=clock)

    await reporter.report(progress(0))
    clock.now = 0.1
    await reporter.finish("Done")

    assert recorder.lines[-1] == "Done"


async def test_a_refused_edit_does_not_break_the_download() -> None:
    # The message is decoration on top of a transfer that is already under way.
    async def edit(_line: str) -> None:
        raise TelegramError("Too Many Requests")

    assert await ProgressReporter(edit).report(progress(0)) is False


async def test_a_failing_final_edit_is_also_survivable() -> None:
    async def edit(_line: str) -> None:
        raise TelegramError("message is not modified")

    assert await ProgressReporter(edit).finish("Done") is False
