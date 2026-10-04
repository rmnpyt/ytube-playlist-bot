"""Working out what a user pasted, before spending a network call on it.

The order of the checks is the design. A playlist sent to the single-video flow is
rejected first and by name, because "that is a playlist" is actionable while
"extractor failed" is not. Everything that is simply not a video link falls through
to one generic message, since a user who pasted the wrong thing does not benefit from
an itemised account of how each character failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs, urlparse

from bot.domain.errors import InvalidLinkError, PlaylistNotAllowedError

VIDEO_ID_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_-]{11}\Z")

_PATH_PREFIXES: Final = ("shorts", "embed", "v", "live")


@dataclass(frozen=True, slots=True)
class ParsedLink:
    """What the text turned out to be."""

    kind: str
    video_id: str | None = None
    playlist_id: str | None = None

    @classmethod
    def video(cls, video_id: str) -> ParsedLink:
        return cls(kind="video", video_id=video_id)

    @property
    def canonical_url(self) -> str:
        """A canonical URL, so a YouTube ID typed by hand still works downstream."""
        if self.video_id is not None:
            return f"https://www.youtube.com/watch?v={self.video_id}"
        if self.playlist_id is not None:
            return f"https://www.youtube.com/playlist?list={self.playlist_id}"
        raise InvalidLinkError("that link contains neither a video nor a playlist.")

    @property
    def is_video(self) -> bool:
        return self.kind == "video"

    @property
    def is_playlist(self) -> bool:
        return self.kind == "playlist"


def _strip_wrapping(text: str) -> str:
    """Tolerate punctuation that comes from copying rather than from the URL."""
    return text.strip().strip("<>").rstrip(".,;)").strip()


def _looks_like_a_url(text: str) -> bool:
    return "/" in text or "?" in text or "." in text


def parse_link(text: str, *, allow_playlist: bool = False) -> ParsedLink:
    """Identify a video, a playlist, or explain why the text is neither.

    Raises:
        PlaylistNotAllowedError: a playlist was sent to a single-video flow.
        InvalidLinkError: the text is not a usable YouTube link.
    """
    candidate = _strip_wrapping(text)

    if not candidate:
        raise InvalidLinkError("Send me a YouTube link to get started.")

    if not _looks_like_a_url(candidate) and VIDEO_ID_PATTERN.match(candidate):
        return ParsedLink.video(candidate)

    url = candidate if "://" in candidate else f"https://{candidate}"
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")

    if host in {"youtube.com", "youtu.be", "youtube-nocookie.com"}:
        result = _from_youtube_url(parsed)
    else:
        result = None

    if result is None:
        raise InvalidLinkError(
            "That is not a YouTube link. Send a youtube.com/watch, youtu.be or "
            "youtube.com/shorts address."
        )

    if result.is_playlist and not allow_playlist:
        raise PlaylistNotAllowedError(
            "That is a playlist. Use the Playlist button to send a whole playlist."
        )

    return result


def _from_youtube_url(parsed: object) -> ParsedLink | None:
    host = getattr(parsed, "hostname", "") or ""
    query = parse_qs(getattr(parsed, "query", "") or "")
    path = getattr(parsed, "path", "") or ""
    segments = [segment for segment in path.split("/") if segment]

    # A list parameter can ride along with a watch URL. A valid video id still wins:
    # the bot has a separate playlist button, and someone who taps "single video" and
    # pastes their address bar means the video, not whatever playlist residue the URL
    # carries. A list only decides the outcome when there is no usable video id, which
    # covers /playlist?list=... and watch?v=<not-an-id>&list=...
    playlist_id = query.get("list", [None])[0]
    video_id = query.get("v", [None])[0]

    if not video_id and host.startswith("youtu.be"):
        video_id = segments[0] if segments else None

    if not video_id and len(segments) >= 2 and segments[0] in _PATH_PREFIXES:
        video_id = segments[1]

    if video_id and VIDEO_ID_PATTERN.match(video_id):
        return ParsedLink.video(video_id)

    if playlist_id:
        return ParsedLink(kind="playlist", playlist_id=playlist_id)

    return None
