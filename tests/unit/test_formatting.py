"""Specification for human-facing number formatting.

Every number the bot shows a user goes through this module, which is why the
expected strings are spelled out rather than computed: the exact rendering is
the requirement, not the arithmetic behind it.
"""

from __future__ import annotations

import pytest

from bot.domain.formatting import (
    format_duration,
    format_eta,
    format_size,
    format_speed,
)


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1_000, "1.0 KB"),
        (48_200_000, "48.2 MB"),
        (2_000_000_000, "2.0 GB"),
        (1_234_000_000_000, "1.23 TB"),
    ],
)
def test_sizes_are_rendered_in_decimal_units(size_bytes: int, expected: str) -> None:
    assert format_size(size_bytes) == expected


def test_sizes_below_a_kilobyte_have_no_decimal() -> None:
    assert format_size(512) == "512 B"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0:00"),
        (5, "0:05"),
        (65, "1:05"),
        (599, "9:59"),
        (3600, "1:00:00"),
        (3_725, "1:02:05"),
    ],
)
def test_durations_render_as_minutes_and_seconds(seconds: int, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_unknown_duration_is_stated_rather_than_guessed() -> None:
    assert format_duration(None) == "unknown length"


def test_unknown_eta_is_stated_rather_than_guessed() -> None:
    assert format_eta(None) == "--:--"


def test_a_known_eta_uses_the_same_shape_as_a_duration() -> None:
    assert format_eta(8) == "00:08"


def test_a_negative_eta_is_clamped_rather_than_shown() -> None:
    # yt-dlp reports a negative ETA while it works out the download rate.
    assert format_eta(-5) == "--:--"


def test_speeds_are_in_megabytes_per_second() -> None:
    assert format_speed(3_145_728) == "3.1 MB/s"


def test_an_unknown_speed_is_left_dash() -> None:
    assert format_speed(None) == "-- MB/s"
