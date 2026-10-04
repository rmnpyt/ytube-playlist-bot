"""What the bot remembers about the people using it.

Access is decided in one place, because "may this user use the bot" has three inputs
that can each say yes: the configured allowlist, the admin list, and an approval an
admin recorded in the database. A handler that checks only one of them will lock out
everybody approved through the other route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.services.storage.repositories import UserRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class User:
    """Somebody the bot knows about."""

    telegram_id: int
    username: str | None = None
    send_as_video: bool = True
    is_allowed: bool = False


@dataclass(frozen=True, slots=True)
class Sighting:
    """Who is talking to the bot, before anything is known about their settings."""

    telegram_id: int
    username: str | None = None


class UserService:
    """Reads and writes user records, and answers whether a user may proceed."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, allowed_ids: frozenset[int]
    ) -> None:
        self._sessions = sessions
        self._allowed_ids = allowed_ids

    async def record_sighting(self, sighting: Sighting) -> None:
        """Note a user on sight, creating their row if this is new to us.

        Called for every update rather than every request, so the bot has a record to
        show an admin before anybody has asked for anything.
        """
        async with self._sessions() as session:
            await UserRepository(session).record_sighting(
                sighting.telegram_id,
                username=sighting.username,
            )
            await session.commit()

    async def get(self, telegram_id: int) -> User | None:
        async with self._sessions() as session:
            row = await UserRepository(session).get(telegram_id)
            if row is None:
                return None
            return User(
                telegram_id=row.telegram_id,
                username=row.username,
                send_as_video=row.send_as_video,
                is_allowed=row.is_allowed,
            )

    async def prefers_video(self, telegram_id: int) -> bool:
        """Whether to deliver as a video. Defaults to video for an unknown user.

        A new user has no row yet, and video is the delivery most of them can play, so
        the safe answer is the default rather than blocking the first request.
        """
        user = await self.get(telegram_id)
        return True if user is None else user.send_as_video

    async def set_prefers_video(self, telegram_id: int, send_as_video: bool) -> None:
        async with self._sessions() as session:
            repository = UserRepository(session)
            await repository.record_sighting(telegram_id)
            await repository.set_send_as_video(telegram_id, send_as_video)
            await session.commit()

    async def may_use(self, telegram_id: int) -> bool:
        """Whether this user is allowed to make the bot do work."""
        if telegram_id in self._allowed_ids:
            return True
        user = await self.get(telegram_id)
        return False if user is None else user.is_allowed

    async def set_allowed(self, telegram_id: int, is_allowed: bool) -> None:
        async with self._sessions() as session:
            repository = UserRepository(session)
            await repository.record_sighting(telegram_id)
            await repository.set_allowed(telegram_id, is_allowed)
            await session.commit()

    async def list_users(self) -> list[User]:
        async with self._sessions() as session:
            rows = await UserRepository(session).all()
            return [
                User(
                    telegram_id=row.telegram_id,
                    username=row.username,
                    send_as_video=row.send_as_video,
                    is_allowed=row.is_allowed,
                )
                for row in rows
            ]
