"""Specification for the user repository.

The behaviour worth protecting is not the SQL; it is that a preference and an approval
survive the process being restarted, which is the whole reason there is a database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from bot.services.storage.db import Database
from bot.services.storage.migrations import apply_migrations
from bot.services.storage.repositories import UserRepository


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await apply_migrations(db.url)
    yield db
    await db.dispose()


async def test_an_unknown_user_has_no_record_yet(database: Database) -> None:
    async with database.session_scope() as session:
        assert await UserRepository(session).get(4242) is None


async def test_recording_a_first_sight_of_a_user(database: Database) -> None:
    async with database.session_scope() as session:
        repository = UserRepository(session)
        user = await repository.record_sighting(4242, username="rmnpy")

    assert user.telegram_id == 4242
    assert user.username == "rmnpy"


async def test_video_is_the_default_delivery_method(database: Database) -> None:
    async with database.session_scope() as session:
        user = await UserRepository(session).record_sighting(4242)

    assert user.send_as_video is True


async def test_a_second_sighting_updates_the_name_without_a_duplicate(
    database: Database,
) -> None:
    async with database.session_scope() as session:
        repository = UserRepository(session)
        await repository.record_sighting(4242, username="old_name")
        user = await repository.record_sighting(4242, username="new_name")

    async with database.session_scope() as session:
        everyone = await UserRepository(session).all()

    assert user.username == "new_name"
    assert [row.telegram_id for row in everyone] == [4242]


async def test_a_delivery_preference_is_stored_and_read_back(database: Database) -> None:
    async with database.session_scope() as session:
        repository = UserRepository(session)
        await repository.record_sighting(4242)
        await repository.set_send_as_video(4242, False)

    async with database.session_scope() as session:
        user = await UserRepository(session).get(4242)

    assert user is not None
    assert user.send_as_video is False


async def test_a_preference_survives_a_restart(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"
    first = Database(url)
    await apply_migrations(url)
    async with first.session_scope() as session:
        repository = UserRepository(session)
        await repository.record_sighting(4242, username="rmnpy")
        await repository.set_send_as_video(4242, False)
    await first.dispose()

    # A brand new engine against the same file, as if the process had restarted.
    second = Database(url)
    async with second.session_scope() as session:
        user = await UserRepository(session).get(4242)
    await second.dispose()

    assert user is not None
    assert user.username == "rmnpy"
    assert user.send_as_video is False


async def test_setting_a_preference_for_someone_never_seen_is_rejected(
    database: Database,
) -> None:
    # Silently creating the row would hide a bug in the caller's flow, and would skip
    # whatever a first sighting is meant to record.
    with pytest.raises(LookupError):
        async with database.session_scope() as session:
            await UserRepository(session).set_send_as_video(9999, False)


async def test_the_allowlist_is_recorded_on_the_user(database: Database) -> None:
    async with database.session_scope() as session:
        repository = UserRepository(session)
        await repository.record_sighting(4242)
        await repository.set_allowed(4242, True)

    async with database.session_scope() as session:
        user = await UserRepository(session).get(4242)

    assert user is not None
    assert user.is_allowed is True
