"""Specification for quality options and the menu that filters them.

The contract requires that an option above the ceiling is absent from the menu and
that the menu says which ones were removed and why. Both halves matter: hiding
silently is worse than refusing, because the user concludes the bot cannot do 1080p
when in fact the bot declined to try.
"""

from __future__ import annotations

from bot.domain.models import (
    DownloadProgress,
    QualityMenu,
    QualityOption,
    RemovalReason,
    VideoMetadata,
)

MB = 1_000_000
LIMIT = 2_000 * MB


def option(height: int, size_bytes: int, format_id: str | None = None) -> QualityOption:
    return QualityOption(
        format_id=format_id or f"f{height}",
        height=height,
        size_bytes=size_bytes,
    )


def test_a_button_shows_resolution_and_size() -> None:
    assert option(720, 48_200_000).label == "720p — 48.2 MB"


def test_options_are_ordered_best_first() -> None:
    menu = QualityMenu(options=(option(1080, 200 * MB), option(360, 20 * MB)))

    assert [item.height for item in menu.options] == [1080, 360]


def test_an_option_over_the_limit_is_removed_and_explained() -> None:
    menu = QualityMenu(
        options=(option(720, 48 * MB),),
        removed=(
            QualityMenu.Removed(
                option=option(2160, 2_400 * MB),
                reason=RemovalReason.TOO_LARGE,
            ),
        ),
        limit_bytes=LIMIT,
    )

    assert [item.height for item in menu.options] == [720]


def test_the_removal_note_names_the_count_and_the_limit() -> None:
    menu = QualityMenu(
        options=(option(720, 48 * MB),),
        removed=(
            QualityMenu.Removed(option(2160, 2_400 * MB), RemovalReason.TOO_LARGE),
            QualityMenu.Removed(option(1440, 1_900 * MB), RemovalReason.TOO_LARGE),
        ),
        limit_bytes=LIMIT,
    )

    note = menu.removal_note
    assert "2" in note
    assert "2.0 GB" in note


def test_the_removal_note_is_empty_when_nothing_was_removed() -> None:
    menu = QualityMenu(options=(option(720, 48 * MB),))

    assert menu.removal_note == ""


def test_an_option_of_unknown_size_is_named_as_such_when_removed() -> None:
    menu = QualityMenu(
        options=(option(720, 48 * MB),),
        removed=(
            QualityMenu.Removed(
                option=option(1080, 0),
                reason=RemovalReason.UNKNOWN_SIZE,
                known_size=False,
            ),
        ),
    )

    assert "size" in menu.removal_note


def test_a_menu_with_no_options_is_reported_as_empty() -> None:
    assert QualityMenu(options=()).is_empty


def test_a_menu_with_options_is_not_empty() -> None:
    assert not QualityMenu(options=(option(360, 20 * MB),)).is_empty


def test_metadata_renders_a_duration_for_the_header() -> None:
    metadata = VideoMetadata(video_id="abc", title="A Song", duration_seconds=225)

    assert metadata.caption == "A Song · 3:45"


def test_metadata_without_a_duration_still_renders() -> None:
    metadata = VideoMetadata(video_id="abc", title="A Song", duration_seconds=None)

    assert metadata.caption == "A Song · unknown length"


def test_progress_renders_the_agreed_line() -> None:
    progress = DownloadProgress(
        downloaded_bytes=22_400_000,
        total_bytes=48_200_000,
        speed_bytes_per_second=3_145_728,
        eta_seconds=8,
    )

    assert progress.line == "⬇ 46% · 22.4 MB/48.2 MB · 3.1 MB/s · ETA 00:08"


def test_progress_without_a_total_omits_the_total_and_the_percentage() -> None:
    progress = DownloadProgress(
        downloaded_bytes=22_400_000,
        total_bytes=None,
        speed_bytes_per_second=None,
        eta_seconds=None,
    )

    assert progress.line == "⬇ · 22.4 MB · -- MB/s · ETA --:--"


def test_progress_never_reports_more_than_a_hundred_percent() -> None:
    # yt-dlp can overshoot the expected total mid-transfer, and "⬇ 103%" is a lie.
    progress = DownloadProgress(
        downloaded_bytes=50,
        total_bytes=48,
        speed_bytes_per_second=None,
        eta_seconds=None,
    )

    assert progress.percent == 100
