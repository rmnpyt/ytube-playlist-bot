"""Specification for the database engine and its session lifecycle.

These tests use a real SQLite file in a temporary directory rather than mocks. The
whole point of this layer is that a file survives the process, so an in-memory fake
would test the wrong thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from bot.services.storage.db import Database


def make_database(tmp_path: Path) -> Database:
    return Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")


async def create_schema(db: Database) -> None:
    async with db.session_scope() as session:
        await session.execute(text("create table probe (id integer primary key)"))


async def test_a_session_commits_its_work(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    await create_schema(db)

    async with db.session_scope() as session:
        await session.execute(text("insert into probe (id) values (1)"))

    async with db.session_scope() as session:
        found = (await session.execute(text("select count(*) from probe"))).scalar_one()
    await db.dispose()

    assert found == 1


async def test_a_failure_rolls_the_work_back(tmp_path: Path) -> None:
    # The bot must not leave half a selection or a half-written preference behind
    # because something later in the request failed.
    db = make_database(tmp_path)
    await create_schema(db)

    with pytest.raises(RuntimeError):
        async with db.session_scope() as session:
            await session.execute(text("insert into probe (id) values (1)"))
            raise RuntimeError("something downstream failed")

    async with db.session_scope() as session:
        found = (await session.execute(text("select count(*) from probe"))).scalar_one()
    await db.dispose()

    assert found == 0


async def test_a_failed_statement_does_not_poison_later_work(tmp_path: Path) -> None:
    db = make_database(tmp_path)
    await create_schema(db)

    with pytest.raises(IntegrityError):
        async with db.session_scope() as session:
            await session.execute(text("insert into probe (id) values (1)"))
            await session.execute(text("insert into probe (id) values (1)"))

    async with db.session_scope() as session:
        await session.execute(text("insert into probe (id) values (2)"))
        found = (await session.execute(text("select id from probe"))).scalars().all()
    await db.dispose()

    assert list(found) == [2]


async def test_writes_are_durable_after_the_engine_is_closed(tmp_path: Path) -> None:
    # This is the contract point: a restart must not lose an approval or a preference.
    db = make_database(tmp_path)
    await create_schema(db)
    async with db.session_scope() as session:
        await session.execute(text("insert into probe (id) values (7)"))
    await db.dispose()

    reopened = make_database(tmp_path)
    async with reopened.session_scope() as session:
        found = (await session.execute(text("select id from probe"))).scalar_one()
    await reopened.dispose()

    assert found == 7


async def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    # SQLite ignores foreign keys unless it is told not to, and the default is off.
    db = make_database(tmp_path)
    async with db.session_scope() as session:
        await session.execute(
            text("create table parent (id integer primary key)"),
        )
        await session.execute(
            text(
                "create table child (id integer primary key, "
                "parent_id integer references parent(id))",
            ),
        )

    with pytest.raises(IntegrityError):
        async with db.session_scope() as session:
            await session.execute(text("insert into child (id, parent_id) values (1, 999)"))

    await db.dispose()


async def test_two_engines_can_write_to_the_same_file(tmp_path: Path) -> None:
    # Two instances of the bot must not corrupt each other's writes.
    first = make_database(tmp_path)
    second = make_database(tmp_path)
    await create_schema(first)

    async with first.session_scope() as session:
        await session.execute(text("insert into probe (id) values (1)"))
    async with second.session_scope() as session:
        await session.execute(text("insert into probe (id) values (2)"))

    async with first.session_scope() as session:
        found = (await session.execute(text("select id from probe order by id"))).scalars().all()
    await first.dispose()
    await second.dispose()

    assert list(found) == [1, 2]
