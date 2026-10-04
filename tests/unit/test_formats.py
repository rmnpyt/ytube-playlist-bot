"""Specification for turning yt-dlp's format list into a quality menu.

Three rules do all the work, and each has a failure mode worth stating:

1. Progressive only. A video-only stream has no sound, and sending one labelled 1080p
   is worse than not offering 1080p.
2. One option per height, keeping the largest. YouTube offers several bitrates at the
   same height; offering all of them fills the menu with indistinguishable buttons.
3. Sizes must be known, and must fit. An option over the ceiling is removed, not
   offered and then rejected at upload time.

The inputs below are the shape yt-dlp actually returns: a list of dicts, keys missing
in all the ways that matter.
"""

from __future__ import annotations

from typing import Any

import pytest

from bot.domain.errors import AllOptionsTooLargeError, NoDownloadableOptionsError
from bot.services.youtube.formats import build_quality_menu

MB = 1_000_000
LIMIT = 2_000 * MB


def fmt(
    format_id: str,
    *,
    height: int | None,
    vcodec: str = "avc1",
    acodec: str = "mp4a",
    filesize: int | None = None,
    filesize_approx: int | None = None,
    protocol: str = "https",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "format_id": format_id,
        "vcodec": vcodec,
        "acodec": acodec,
        "protocol": protocol,
        "ext": "mp4",
    }
    if height is not None:
        entry["height"] = height
    if filesize is not None:
        entry["filesize"] = filesize
    if filesize_approx is not None:
        entry["filesize_approx"] = filesize_approx
    return entry


def test_progressive_formats_become_options() -> None:
    menu = build_quality_menu(
        formats=[fmt("18", height=360, filesize=20 * MB)],
        limit_bytes=LIMIT,
    )

    assert [option.label for option in menu.options] == ["360p — 20.0 MB"]


def test_video_only_formats_are_not_offered() -> None:
    # Nothing to send, so this is an explanation rather than an empty keyboard.
    with pytest.raises(NoDownloadableOptionsError):
        build_quality_menu(
            formats=[fmt("137", height=1080, vcodec="avc1", acodec="none", filesize=200 * MB)],
            limit_bytes=LIMIT,
        )


def test_audio_only_formats_are_not_offered() -> None:
    with pytest.raises(NoDownloadableOptionsError):
        build_quality_menu(
            formats=[fmt("140", height=None, vcodec="none", acodec="mp4a", filesize=5 * MB)],
            limit_bytes=LIMIT,
        )


def test_several_bitrates_at_one_height_collapse_to_the_largest() -> None:
    menu = build_quality_menu(
        formats=[
            fmt("a", height=720, filesize=40 * MB),
            fmt("b", height=720, filesize=60 * MB),
            fmt("c", height=720, filesize=50 * MB),
        ],
        limit_bytes=LIMIT,
    )

    assert [option.format_id for option in menu.options] == ["b"]


def test_options_are_ordered_from_best_to_worst() -> None:
    menu = build_quality_menu(
        formats=[
            fmt("a", height=360, filesize=20 * MB),
            fmt("b", height=1080, filesize=200 * MB),
            fmt("c", height=720, filesize=60 * MB),
        ],
        limit_bytes=LIMIT,
    )

    assert [option.height for option in menu.options] == [1080, 720, 360]


def test_an_approximate_size_is_used_when_the_exact_one_is_missing() -> None:
    menu = build_quality_menu(
        formats=[fmt("a", height=720, filesize_approx=48 * MB)],
        limit_bytes=LIMIT,
    )

    assert menu.options[0].size_bytes == 48 * MB


def test_an_exact_size_wins_over_an_approximate_one() -> None:
    menu = build_quality_menu(
        formats=[fmt("a", height=720, filesize=48 * MB, filesize_approx=60 * MB)],
        limit_bytes=LIMIT,
    )

    assert menu.options[0].size_bytes == 48 * MB


def test_an_option_over_the_ceiling_is_removed_rather_than_offered() -> None:
    menu = build_quality_menu(
        formats=[
            fmt("a", height=720, filesize=48 * MB),
            fmt("b", height=2160, filesize=2_400 * MB),
        ],
        limit_bytes=LIMIT,
    )

    assert [option.height for option in menu.options] == [720]
    assert menu.removed[0].option.height == 2160


def test_an_option_exactly_at_the_ceiling_is_offered() -> None:
    # Telegram rejects "over" the limit, not "at" it, and rounding it away would
    # make a 2 GB upload impossible for no reason.
    menu = build_quality_menu(formats=[fmt("a", height=1080, filesize=LIMIT)], limit_bytes=LIMIT)

    assert [option.height for option in menu.options] == [1080]
    assert not menu.removed


def test_unknown_sizes_are_hidden_by_default() -> None:
    with pytest.raises(NoDownloadableOptionsError) as caught:
        build_quality_menu(
            formats=[fmt("a", height=1080)],
            limit_bytes=LIMIT,
            allow_unknown_sizes=False,
        )

    assert "size" in str(caught.value)


def test_unknown_sizes_are_offered_when_explicitly_enabled() -> None:
    menu = build_quality_menu(
        formats=[fmt("a", height=1080)],
        limit_bytes=LIMIT,
        allow_unknown_sizes=True,
    )

    assert [option.height for option in menu.options] == [1080]


def test_an_unknown_size_is_hidden_even_when_others_are_allowed() -> None:
    menu = build_quality_menu(
        formats=[
            fmt("a", height=1080),
            fmt("b", height=720, filesize=48 * MB),
        ],
        limit_bytes=LIMIT,
        allow_unknown_sizes=False,
    )

    assert [option.height for option in menu.options] == [720]
    assert menu.removed[0].known_size is False


def test_only_mp4_is_considered() -> None:
    # Downloading webm would need a container the user may not be able to play, and
    # the send-as-video path only accepts mp4.
    with pytest.raises(NoDownloadableOptionsError):
        build_quality_menu(
            formats=[
                {
                    "format_id": "a",
                    "height": 720,
                    "vcodec": "vp9",
                    "acodec": "opus",
                    "ext": "webm",
                    "filesize": 40 * MB,
                }
            ],
            limit_bytes=LIMIT,
        )


def test_a_format_without_a_height_is_skipped() -> None:
    menu = build_quality_menu(
        formats=[
            fmt("a", height=None, filesize=10 * MB),
            fmt("b", height=720, filesize=48 * MB),
        ],
        limit_bytes=LIMIT,
    )

    assert [option.height for option in menu.options] == [720]


def test_no_progressive_formats_at_all_is_reported_clearly() -> None:
    with pytest.raises(NoDownloadableOptionsError):
        build_quality_menu(
            formats=[fmt("137", height=1080, acodec="none", filesize=200 * MB)],
            limit_bytes=LIMIT,
        )


def test_every_option_too_large_is_reported_with_the_numbers() -> None:
    with pytest.raises(AllOptionsTooLargeError) as caught:
        build_quality_menu(
            formats=[fmt("a", height=2160, filesize=2_400 * MB)],
            limit_bytes=LIMIT,
        )

    assert caught.value.largest_bytes == 2_400 * MB
    assert caught.value.limit_bytes == LIMIT


def test_every_option_unknown_sized_is_reported_as_such() -> None:
    # Distinct from "everything is too large": there is no way to know, and telling
    # the user they are over the limit would be a lie.
    with pytest.raises(NoDownloadableOptionsError) as caught:
        build_quality_menu(
            formats=[fmt("a", height=1080)],
            limit_bytes=LIMIT,
            allow_unknown_sizes=False,
        )

    message = str(caught.value)
    assert "did not report the size" in message
    assert "upload limit" in message


def test_an_empty_format_list_is_reported_clearly() -> None:
    with pytest.raises(NoDownloadableOptionsError):
        build_quality_menu(formats=[], limit_bytes=LIMIT)
