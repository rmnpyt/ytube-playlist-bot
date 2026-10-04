"""How often the bot is allowed to rewrite its progress message.

yt-dlp calls back several times a second. Telegram tolerates roughly one edit per
second per chat before it starts refusing with Too Many Requests, and a refusal that
escapes into the download would kill a transfer that is otherwise fine. So progress is
throttled here, where it can be tested with an injected clock instead of with sleeps.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from telegram.error import TelegramError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bot.domain.models import DownloadProgress


class ProgressThrottle:
    """Allows one event through per interval."""

    def __init__(self, interval_seconds: float) -> None:
        self._interval = interval_seconds
        self._last_allowed: float | None = None

    def allows(self, now: float) -> bool:
        """Whether an event at ``now`` may be shown.

        The first event always passes: a user who has just pressed a button should not
        watch a silent message while the throttle decides they waited long enough.
        """
        if self._last_allowed is not None and now - self._last_allowed < self._interval:
            return False
        self._last_allowed = now
        return True


class ProgressReporter:
    """Renders progress onto one message, at a polite rate.

    Edit failures are swallowed on purpose. The message is decoration on top of a
    transfer that is already under way, and a rate limit or a message that turns out to
    be uneditable is no reason to abort the download the user actually asked for.
    """

    def __init__(
        self,
        edit: Callable[[str], Awaitable[None]],
        *,
        interval_seconds: float = 2.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._edit = edit
        self._throttle = ProgressThrottle(interval_seconds)
        self._clock = clock if clock is not None else time.monotonic

    async def report(self, progress: DownloadProgress) -> bool:
        """Show a progress line if the throttle allows it. Whether it was shown."""
        if not self._throttle.allows(self._clock()):
            return False
        return await self._try_edit(progress.line)

    async def finish(self, line: str) -> bool:
        """Write the last line, regardless of the throttle.

        Downloaded bytes are exact at the end and the percentage often never reached
        100, so this is what the user is left looking at.
        """
        return await self._try_edit(line)

    async def _try_edit(self, line: str) -> bool:
        try:
            await self._edit(line)
        except TelegramError:
            return False
        return True
