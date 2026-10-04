"""Specification for recognising what a user pasted.

Parsing happens before any network call, so that sending a playlist link to the
single-video flow produces an immediate, specific message instead of a slow,
confusing failure from yt-dlp. Every rejection below is a case that would otherwise
be reported as "something went wrong".
"""

from __future__ import annotations

import pytest

from bot.domain.errors import InvalidLinkError, PlaylistNotAllowedError
from bot.services.youtube.parsing import ParsedLink, parse_link

VIDEO_ID = "dQw4w9WgXcQ"


def test_a_watch_url_yields_the_video_id() -> None:
    parsed = parse_link(f"https://www.youtube.com/watch?v={VIDEO_ID}")

    assert parsed == ParsedLink.video(VIDEO_ID)


def test_youtu_be_short_urls_are_accepted() -> None:
    assert parse_link(f"https://youtu.be/{VIDEO_ID}").video_id == VIDEO_ID


def test_shorts_are_accepted() -> None:
    assert parse_link(f"https://youtube.com/shorts/{VIDEO_ID}").video_id == VIDEO_ID


def test_embed_urls_are_accepted() -> None:
    assert parse_link(f"https://www.youtube.com/embed/{VIDEO_ID}").video_id == VIDEO_ID


def test_a_scheme_is_optional() -> None:
    # Users paste from the address bar half the time; the scheme is not the point.
    assert parse_link(f"youtube.com/watch?v={VIDEO_ID}").video_id == VIDEO_ID


def test_extra_query_parameters_are_ignored() -> None:
    url = f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42s&list=PLabc&feature=share"

    assert parse_link(url).video_id == VIDEO_ID


def test_a_valid_video_id_wins_over_a_playlist_it_travels_with() -> None:
    # Someone who taps "single video" and pastes their address bar means the video,
    # not the playlist residue in the URL. Only a list decides the outcome when there
    # is no usable video id.
    assert parse_link(f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PLabc123").video_id == (
        VIDEO_ID
    )


def test_surrounding_whitespace_and_punctuation_are_tolerated() -> None:
    assert parse_link(f"  {VIDEO_ID}\n").video_id == VIDEO_ID


def test_a_bare_video_id_is_accepted() -> None:
    assert parse_link(VIDEO_ID).video_id == VIDEO_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PLabc123",
        "https://www.youtube.com/watch?v=abc&list=PLabc123",
    ],
)
def test_a_playlist_is_rejected_by_name(url: str) -> None:
    with pytest.raises(PlaylistNotAllowedError) as caught:
        parse_link(url)

    assert "playlist" in str(caught.value).lower()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "https://vimeo.com/12345",
        "https://youtube.com/watch?v=tooshort",
        "https://youtube.com/@somechannel",
    ],
)
def test_unusable_input_is_rejected(text: str) -> None:
    with pytest.raises(InvalidLinkError):
        parse_link(text)


def test_a_rejected_link_does_not_look_like_an_internal_error() -> None:
    # The user typed something wrong. That is not a bug and must not be logged as one.
    with pytest.raises(InvalidLinkError) as caught:
        parse_link("nonsense")

    assert "Traceback" not in str(caught.value)


def test_the_video_id_is_echoed_back_into_the_canonical_url() -> None:
    parsed = parse_link(f"https://youtu.be/{VIDEO_ID}")

    assert parsed.canonical_url == f"https://www.youtube.com/watch?v={VIDEO_ID}"


def test_a_playlist_link_can_be_parsed_when_the_flow_expects_one() -> None:
    parsed = parse_link("https://www.youtube.com/playlist?list=PLabc123", allow_playlist=True)

    assert parsed.kind == "playlist"
    assert parsed.playlist_id == "PLabc123"


def test_a_video_link_is_still_recognised_when_playlists_are_allowed() -> None:
    # The playlist flow accepts videos too, so this flag must not break the common case.
    parsed = parse_link(f"https://youtu.be/{VIDEO_ID}", allow_playlist=True)

    assert parsed.is_video
