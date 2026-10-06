"""Tests for the DbPlaylistService with a more complete fake session."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bot.services.storage.jobs import DbPlaylistService
from bot.services.storage.orm import JobRow, JobStatus
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RecordingSession:
    """A fake session that records what was executed."""

    def __init__(self) -> None:
        self._jobs: list[JobRow] = []
        self._next_id = 1
        self.commits = 0
        self.rollbacks = 0
        self.executed_selects: list[select] = []

    def add(self, obj: JobRow) -> None:
        if obj.id is None:
            obj.id = len(self._jobs) + 1
        self._jobs.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def scalars(self, query):
        """Execute a select and return matching jobs."""
        whereclause = query.whereclause
        results = []
        for job in self._jobs:
            if self._matches_where(job, whereclause):
                results.append(job)

        class FakeScalars:
            def __init__(self, items):
                self._items = items

            def first(self):
                return self._items[0] if self._items else None

            def all(self):
                return self._items

        return FakeScalars(results)

    def _matches_where(self, job: JobRow, whereclause) -> bool:
        if whereclause is None:
            return True
        if hasattr(whereclause, "clauses"):
            for clause in whereclause.clauses:
                if not self._match_clause(job, clause):
                    return False
            return True
        return self._match_clause(job, whereclause)

    def _match_clause(self, job: JobRow, clause) -> bool:
        # Handle column == value or column IN (...)
        if hasattr(clause, "left") and hasattr(clause, "right"):
            col_name = clause.left.key
            # Handle IN operator
            if hasattr(clause, "operator") and clause.operator.__name__ == "in_op":
                values = clause.right.value if hasattr(clause.right, "value") else clause.right
                return getattr(job, col_name) in values
            # Handle equality
            value = clause.right.value if hasattr(clause.right, "value") else clause.right
            return getattr(job, col_name) == value
        return True

    def begin(self):
        """Return an async context manager for transactions."""
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()
        return False

    async def get(self, model, id_):
        for job in self._jobs:
            if job.id == id_:
                return job
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()
        return False


@pytest.fixture
def fake_session() -> RecordingSession:
    return RecordingSession()


@pytest.fixture
def playlist_service(fake_session: RecordingSession) -> DbPlaylistService:
    from unittest.mock import MagicMock, AsyncMock

    repo = MagicMock()
    repo._session = fake_session

    async def mock_add(chat_id: int, url: str, quality: str) -> None:
        fake_session.add(JobRow(chat_id=chat_id, url=url, quality=quality, status="pending"))

    async def mock_next_pending(chat_id: int):
        for job in fake_session._jobs:
            if job.chat_id == chat_id and job.status == "pending":
                return job
        return None

    repo = MagicMock()
    repo._session = fake_session
    repo.add = AsyncMock(side_effect=lambda chat_id, url, quality: fake_session.add(
        JobRow(chat_id=chat_id, url=url, quality=quality, status="pending")
    ))
    repo.next_pending = AsyncMock(
        side_effect=lambda chat_id: next(
            (job for job in fake_session._jobs
             if job.chat_id == chat_id and job.status == "pending"),
            None
        )
    )

    service = DbPlaylistService.__new__(DbPlaylistService)
    service._repo = repo
    return service


async def test_db_playlist_service_adds_job(playlist_service: DbPlaylistService, fake_session: RecordingSession) -> None:
    await playlist_service.queue(123, "https://youtu.be/abc", "137")

    assert len(fake_session._jobs) == 1
    job = fake_session._jobs[0]
    assert job.chat_id == 123
    assert job.url == "https://youtu.be/abc"
    assert job.quality == "137"
    assert job.status == "pending"


async def test_db_playlist_service_next_pending_returns_first(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    await playlist_service.queue(123, "https://youtu.be/a", "137")
    await playlist_service.queue(123, "https://youtu.be/b", "22")

    job = await playlist_service.next_pending(123)
    assert job == ("https://youtu.be/a", "137")


async def test_db_playlist_service_next_pending_skips_running_and_done(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    await playlist_service.queue(123, "https://youtu.be/a", "137")
    await playlist_service.queue(123, "https://youtu.be/b", "22")

    job_obj = fake_session._jobs[0]
    job_obj.status = "running"

    job = await playlist_service.next_pending(123)
    assert job == ("https://youtu.be/b", "22")


async def test_db_playlist_service_mark_running(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    await playlist_service.queue(123, "https://youtu.be/a", "137")

    await playlist_service.mark_running("https://youtu.be/a", "137")

    job_obj = fake_session._jobs[0]
    assert job_obj.status == "running"


async def test_db_playlist_service_mark_done(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    await playlist_service.queue(123, "https://youtu.be/a", "137")

    await playlist_service.mark_done("https://youtu.be/a", "137")

    job_obj = fake_session._jobs[0]
    assert job_obj.status == "done"


async def test_db_playlist_service_mark_failed(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    await playlist_service.queue(123, "https://youtu.be/a", "137")

    await playlist_service.mark_failed("https://youtu.be/a", "137")

    job_obj = fake_session._jobs[0]
    assert job_obj.status == "failed"


async def test_db_playlist_service_mark_done_not_found(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    """Calling mark_done on a non-existent job should not raise."""
    await playlist_service.mark_done("https://youtu.be/nonexistent", "999")
    # Should not raise, just do nothing


async def test_db_playlist_service_next_pending_empty(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    """next_pending on empty queue returns None."""
    job = await playlist_service.next_pending(999)
    assert job is None


async def test_db_playlist_service_mark_running_not_found(
    playlist_service: DbPlaylistService, fake_session: RecordingSession
) -> None:
    """mark_running on non-existent job does nothing."""
    await playlist_service.mark_running("https://youtu.be/nonexistent", "999")
    # Should not raise