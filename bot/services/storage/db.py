"""The database engine and its session lifecycle.

Two decisions are worth stating outright, because SQLite gets both wrong by default:

* Foreign keys are off unless a connection is told otherwise, so every connection is
  asked for them.
* The default journal mode leaves readers blocking writers, which on a bot means one
  user's long download can stall another's lookup. WAL fixes that.

Creating the engine is cheap and does not touch the disk; the file appears on the first
connection. Nothing here creates tables: that is the migration runner's job, so the
schema has exactly one source of truth.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType


class Database:
    """Owns one engine and hands out sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._url = url
        self._engine: AsyncEngine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        _apply_sqlite_pragmas(self._engine)
        self._sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine,
            # Callers keep using a record after the transaction ends. Without this,
            # reading an attribute outside the scope would trigger a lazy refresh that
            # has no connection to use.
            expire_on_commit=False,
        )

    @property
    def url(self) -> str:
        return self._url

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @contextlib.asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        """One transaction: commit on the way out, roll back on any exception."""
        async with self._sessions() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise
            else:
                await session.commit()

    async def dispose(self) -> None:
        """Close every pooled connection. Required on shutdown."""
        await self._engine.dispose()

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.dispose()


def _apply_sqlite_pragmas(engine: AsyncEngine) -> None:
    if engine.dialect.name != "sqlite":  # pragma: no cover - only SQLite is configured
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()
