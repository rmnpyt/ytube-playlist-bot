"""The database-backed playlist service.

Persists the user's chosen URL + quality pairs so a hard kill cannot leave jobs
stuck. On restart, pending jobs are replayed in FIFO order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.domain.protocols import PlaylistService
from bot.services.storage.repositories import JobRepository, JobStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DbPlaylistService(PlaylistService):
    """Playlist backed by the ``jobs`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = JobRepository(session)

    async def queue(self, chat_id: int, url: str, quality: str) -> None:
        await self._repo.add(chat_id, url, quality)

    async def next_pending(self, chat_id: int) -> tuple[str, str] | None:
        job = await self._repo.next_pending(chat_id)
        if job is None:
            return None
        return (job.url, job.quality)

    async def mark_running(self, url: str, quality: str) -> None:
        # We don't have a direct "find by url+quality" method; for simplicity
        # the caller (handler) knows the job ID from when it queued it.
        # But the protocol doesn't expose IDs. A real implementation would
        # need a way to locate the row. For now, we'll scan pending jobs
        # for a match — acceptable because a chat's queue is small.
        async with self._repo._session.begin():
            from sqlalchemy import select

            from bot.services.storage.orm import JobRow

            result = await self._repo._session.scalars(
                select(JobRow).where(
                    JobRow.url == url,
                    JobRow.quality == quality,
                    JobRow.status == JobStatus.PENDING,
                )
            )
            job = result.first()
            if job is not None:
                job.status = JobStatus.RUNNING

    async def mark_done(self, url: str, quality: str) -> None:
        await self._mark(url, quality, JobStatus.DONE)

    async def mark_failed(self, url: str, quality: str) -> None:
        await self._mark(url, quality, JobStatus.FAILED)

    async def _mark(self, url: str, quality: str, status: str) -> None:
        async with self._repo._session.begin():
            from sqlalchemy import select

            from bot.services.storage.orm import JobRow

            result = await self._repo._session.scalars(
                select(JobRow).where(
                    JobRow.url == url,
                    JobRow.quality == quality,
                    JobRow.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
                )
            )
            job = result.first()
            if job is not None:
                job.status = status
