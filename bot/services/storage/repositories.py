"""Reading and writing the bot's records.

Repositories take a session rather than making one, so the caller decides the
transaction boundary and two repositories can share it. They return ORM records with
their values already loaded; nothing here needs a connection after the scope closes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from bot.services.storage.orm import JobRow, JobStatus, UserRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["JobRepository", "JobStatus", "UserRepository"]


class UserRepository:
    """Access to the ``users`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, telegram_id: int) -> UserRow | None:
        return await self._session.get(UserRow, telegram_id)

    async def all(self) -> list[UserRow]:
        result = await self._session.scalars(select(UserRow).order_by(UserRow.telegram_id))
        return list(result)

    async def record_sighting(self, telegram_id: int, *, username: str | None = None) -> UserRow:
        """Note that this user exists, or refresh what we know about them.

        Deliberately an upsert rather than an insert: Telegram chats start on every
        update, and a first sighting happens far more often than a first request.
        """
        user = await self._session.get(UserRow, telegram_id)
        if user is None:
            user = UserRow(telegram_id=telegram_id, username=username)
            self._session.add(user)
        elif username is not None:
            user.username = username
        await self._session.flush()
        return user

    async def set_send_as_video(self, telegram_id: int, send_as_video: bool) -> None:
        user = await self._require(telegram_id)
        user.send_as_video = send_as_video

    async def set_allowed(self, telegram_id: int, is_allowed: bool) -> None:
        user = await self._require(telegram_id)
        user.is_allowed = is_allowed

    async def _require(self, telegram_id: int) -> UserRow:
        user = await self._session.get(UserRow, telegram_id)
        if user is None:
            # Creating the row here would paper over a caller that skipped recording
            # the user, and would lose whatever a first sighting is meant to capture.
            raise LookupError(f"no record of user {telegram_id}")
        return user


class JobRepository:
    """Access to the ``jobs`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, chat_id: int, url: str, quality: str) -> JobRow:
        job = JobRow(chat_id=chat_id, url=url, quality=quality, status=JobStatus.PENDING)
        self._session.add(job)
        await self._session.flush()
        return job

    async def next_pending(self, chat_id: int) -> JobRow | None:
        result = await self._session.scalars(
            select(JobRow)
            .where(JobRow.chat_id == chat_id, JobRow.status == JobStatus.PENDING)
            .order_by(JobRow.created_at)
            .limit(1)
        )
        return result.first()

    async def set_status(self, job_id: int, status: str) -> None:
        job = await self._session.get(JobRow, job_id)
        if job is not None:
            job.status = status

    async def mark_running(self, job_id: int) -> None:
        await self.set_status(job_id, JobStatus.RUNNING)

    async def mark_done(self, job_id: int) -> None:
        await self.set_status(job_id, JobStatus.DONE)

    async def mark_failed(self, job_id: int) -> None:
        await self.set_status(job_id, JobStatus.FAILED)
