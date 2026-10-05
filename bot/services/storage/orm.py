"""The database tables, as SQLAlchemy sees them.

These classes are storage, not domain: handlers should receive plain values or the
small records the repositories return, never a live ORM object it might accidentally
mutate and commit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Timezone-aware now, because a naive timestamp is ambiguous in a log."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """The declarative base every table hangs off."""


class UserRow(Base):
    """Somebody who has interacted with the bot, and their delivery preference."""

    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The plan's default is a video; a document is the fallback for formats Telegram
    # will not accept as a video.
    send_as_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"UserRow(telegram_id={self.telegram_id!r}, send_as_video={self.send_as_video!r})"


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobRow(Base):
    """One playlist entry waiting to be downloaded."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    quality: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"JobRow(id={self.id!r}, chat_id={self.chat_id!r}, status={self.status!r})"
