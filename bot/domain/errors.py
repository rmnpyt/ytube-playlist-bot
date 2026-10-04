"""Errors the domain can raise.

The split matters more than the individual types. ``BotError`` means the bot knows
what went wrong and has something useful to say to the user. Anything else is a bug
or an upstream failure, gets logged with detail, and reaches the user as a generic
apology, because "Traceback" in a chat is never the helpful answer.
"""

from __future__ import annotations


class BotError(Exception):
    """A failure with a message the user can be shown unchanged."""


class InternalError(BotError):
    """A failure the user should not be told about.

    Deliberately still a ``BotError``: handlers catch that base class and format a
    message. It is separated so the error handler can log these with a stack trace
    while staying silent about the detail.
    """


class InvalidLinkError(BotError):
    """The text sent was not a usable YouTube link."""


class PlaylistNotAllowedError(BotError):
    """A playlist was sent where the flow expects a single video."""


class VideoUnavailableError(BotError):
    """YouTube refused to describe the video, or the video is gone."""


class NoDownloadableOptionsError(BotError):
    """No progressive format survived filtering, so there is nothing to offer."""


class UnknownSizeNotAllowedError(BotError):
    """Every option lacked a known size and unknown sizes are disabled."""


class AllOptionsTooLargeError(BotError):
    """Every option exceeded the upload ceiling, so none can be offered."""

    def __init__(self, largest_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"the largest available quality is {largest_bytes} bytes, "
            f"over the {limit_bytes} byte upload limit"
        )
        self.largest_bytes = largest_bytes
        self.limit_bytes = limit_bytes


class NotAllowedError(BotError):
    """The user is not permitted to use the bot."""


class RateLimitedError(BotError):
    """The user has used their allowance for the current window."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"try again in {retry_after_seconds} seconds")
        self.retry_after_seconds = retry_after_seconds


class DownloadFailedError(InternalError):
    """yt-dlp or ffmpeg failed. The detail is for the log, not the chat."""
