"""Specification for the migration runner.

A migration that has never been executed is a rumour. These tests run the real Alembic
environment against a temporary database, so the committed schema is proven to be
creatable and to say what it claims.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from bot.services.storage.db import Database
from bot.services.storage.migrations import apply_migrations
from bot.services.storage.orm import Base

EXPECTED_USER_COLUMNS = {
    "telegram_id",
    "username",
    "send_as_video",
    "is_allowed",
    "created_at",
    "updated_at",
}


async def migrated_database(tmp_path: Path) -> Database:
    url = f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"
    await apply_migrations(url)
    return Database(url)


async def table_names(db: Database) -> set[str]:
    async with db.engine.connect() as connection:
        names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    return set(names)


async def column_names(db: Database, table: str) -> set[str]:
    async with db.engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: [column["name"] for column in inspect(sync).get_columns(table)],
        )
    return set(columns)


async def test_upgrading_creates_the_tables_the_bot_needs(tmp_path: Path) -> None:
    db = await migrated_database(tmp_path)

    tables = await table_names(db)
    await db.dispose()

    assert "users" in tables


async def test_the_user_table_carries_the_columns_handlers_read(tmp_path: Path) -> None:
    db = await migrated_database(tmp_path)

    columns = await column_names(db, "users")
    await db.dispose()

    assert columns == EXPECTED_USER_COLUMNS


async def test_upgrading_twice_is_harmless(tmp_path: Path) -> None:
    # The bot must be able to start against a database that is already current.
    url = f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"

    await apply_migrations(url)
    await apply_migrations(url)


async def test_a_database_can_be_created_in_its_parent_directory(tmp_path: Path) -> None:
    # The container's data directory is created by the volume, but a developer running
    # from a checkout should not have to make the directory by hand.
    url = f"sqlite+aiosqlite:///{tmp_path / 'nested' / 'deeper' / 'bot.db'}"

    await apply_migrations(url)

    assert (tmp_path / "nested" / "deeper" / "bot.db").exists()


async def test_the_models_and_the_migration_agree(tmp_path: Path) -> None:
    # Without this, adding a column to orm.py and forgetting the migration is a bug
    # that only shows up as a runtime error in the container.
    db = await migrated_database(tmp_path)

    tables = await table_names(db)
    migrated: dict[str, set[str]] = {}
    for table in Base.metadata.tables:
        migrated[table] = await column_names(db, table)
    await db.dispose()

    declared = {
        name: {column.name for column in table.columns}
        for name, table in Base.metadata.tables.items()
    }
    assert tables >= set(declared)
    assert migrated == declared
