"""Running the migrations from code.

Alembic owns the schema. The bot calls this once at startup so a fresh container and an
existing database both end up current, and a developer never has to remember a command.

Alembic is synchronous, so :func:`apply_migrations` is the async entry point and it
offloads the work rather than blocking the loop that is about to start polling.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


async def apply_migrations(database_url: str) -> None:
    """Bring the database up to the latest revision, without blocking the loop."""
    _create_sqlite_directory(database_url)
    await asyncio.to_thread(upgrade_to_head, database_url)


def upgrade_to_head(database_url: str) -> None:
    """Bring the database at ``database_url`` up to the latest revision, synchronously."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # The URL comes from the environment, never from the checked-in ini file, so a
    # secret or a machine-specific path cannot be committed by accident. The async
    # driver is stripped because Alembic connects synchronously; handing it
    # "sqlite+aiosqlite" would build an async engine outside a greenlet.
    config.set_main_option("sqlalchemy.url", _sync_url(database_url))
    command.upgrade(config, "head")


def _create_sqlite_directory(database_url: str) -> None:
    """SQLite will not create the directory it is asked to write into."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _sync_url(database_url: str) -> str:
    """Alembic's own connection is synchronous, so the async driver is swapped out."""
    return database_url.replace("+aiosqlite", "").replace("+asyncpg", "")
