"""Value objects shared by handlers, services and storage.

Nothing here knows about Telegram or yt-dlp. That is the point: these are the terms
the rest of the bot argues about, and keeping them free of framework types is what
lets the quality filter be tested without a bot, a network, or a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.domain.formatting import format_duration, format_eta, format_size, format_speed


class RemovalReason(Enum):
    """Why a real, usable format is not being offered."""

    TOO_LARGE = "too_large"
    UNKNOWN_SIZE = "unknown_size"


@dataclass(frozen=True, slots=True, order=True)
class QualityOption:
    """One downloadable progressive format, as shown on a button."""

    format_id: str
    height: int
    size_bytes: int

    @property
    def label(self) -> str:
        return f"{self.height}p — {format_size(self.size_bytes)}"


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    """What we know about a video before the user picks a quality."""

    video_id: str
    title: str
    duration_seconds: int | None = None
    thumbnail_url: str | None = None

    @property
    def caption(self) -> str:
        return f"{self.title} · {format_duration(self.duration_seconds)}"


@dataclass(frozen=True, slots=True)
class VideoDescription:
    """Everything a handler needs to render the quality menu for one video."""

    metadata: VideoMetadata
    menu: QualityMenu


@dataclass(frozen=True, slots=True)
class QualityMenu:
    """Offered options, plus the ones deliberately withheld and why.

    Carrying the removals alongside the options is deliberate. A menu that silently
    omits a quality reads as a bot that cannot handle it, and the user has no way to
    tell the difference between that and a real limit.
    """

    options: tuple[QualityOption, ...]
    removed: tuple[QualityMenu.Removed, ...] = ()
    limit_bytes: int = 0

    @dataclass(frozen=True, slots=True)
    class Removed:
        option: QualityOption
        reason: RemovalReason
        known_size: bool = True

    @property
    def is_empty(self) -> bool:
        return not self.options

    @property
    def largest_removed(self) -> QualityOption | None:
        """The biggest option that was withheld for being over the limit."""
        sized = [item.option for item in self.removed if item.known_size]
        return max(sized, key=lambda option: option.size_bytes, default=None)

    @property
    def removal_note(self) -> str:
        """One line explaining what is missing, or nothing if nothing is."""
        if not self.removed:
            return ""

        too_large = sum(1 for item in self.removed if item.reason is RemovalReason.TOO_LARGE)
        unknown = sum(1 for item in self.removed if item.reason is RemovalReason.UNKNOWN_SIZE)

        parts: list[str] = []
        if too_large:
            parts.append(f"{too_large} over the {format_size(self.limit_bytes)} upload limit")
        if unknown:
            parts.append(f"{unknown} with an unknown size")
        return f"Removed {', '.join(parts)}. Pick one below to continue."


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    """A snapshot of a transfer, used to render one progress line."""

    downloaded_bytes: int
    total_bytes: int | None
    speed_bytes_per_second: float | None
    eta_seconds: int | None

    @property
    def percent(self) -> int | None:
        if not self.total_bytes:
            return None
        return min(int(self.downloaded_bytes / self.total_bytes * 100), 100)

    @property
    def line(self) -> str:
        percent = self.percent
        head = f"⬇ {percent}%" if percent is not None else "⬇"
        total = f"/{format_size(self.total_bytes)}" if self.total_bytes else ""
        return " · ".join(
            [
                head,
                f"{format_size(self.downloaded_bytes)}{total}",
                format_speed(self.speed_bytes_per_second),
                f"ETA {format_eta(self.eta_seconds)}",
            ]
        )
