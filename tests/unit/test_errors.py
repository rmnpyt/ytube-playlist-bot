"""Specification for the domain errors a user can trigger.

Each one has to be distinguishable, because each one earns a different message. A
single "something went wrong" is what makes a bot feel broken.
"""

from __future__ import annotations

from bot.domain import errors


def test_every_user_facing_error_is_a_bot_error() -> None:
    user_facing = (
        errors.InvalidLinkError,
        errors.PlaylistNotAllowedError,
        errors.VideoUnavailableError,
        errors.AllOptionsTooLargeError,
        errors.NoDownloadableOptionsError,
        errors.UnknownSizeNotAllowedError,
        errors.NotAllowedError,
        errors.RateLimitedError,
    )

    for error_type in user_facing:
        assert issubclass(error_type, errors.BotError)


def test_internal_errors_are_marked_so_the_handler_can_hide_their_detail() -> None:
    # InternalError stays a BotError so one except clause catches everything, but it
    # is marked so the handler knows to log the detail instead of quoting it back.
    # The second assertion guards the other direction: an ordinary bad link must
    # never be classified as internal, or the user gets an apology instead of help.
    assert issubclass(errors.InternalError, errors.BotError)
    assert not issubclass(errors.InvalidLinkError, errors.InternalError)


def test_an_error_carries_its_own_message() -> None:
    error = errors.InvalidLinkError("That does not look like a YouTube link.")

    assert str(error) == "That does not look like a YouTube link."


def test_rate_limit_errors_state_when_the_user_may_try_again() -> None:
    error = errors.RateLimitedError(retry_after_seconds=42)

    assert error.retry_after_seconds == 42


def test_too_large_errors_name_the_largest_offender() -> None:
    error = errors.AllOptionsTooLargeError(largest_bytes=2_400_000_000, limit_bytes=2_000_000_000)

    assert error.largest_bytes > error.limit_bytes
