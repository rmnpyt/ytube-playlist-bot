"""Specification for what the bot remembers about users.

These run against a real SQLite file rather than a stub, because the interesting
behaviour is what survives a restart: an approval recorded yesterday has to still be
there today, or the bot quietly stops being usable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from bot.services.storage.db import Database
from bot.services.storage.migrations import apply_migrations
from bot.services.users import Sighting, UserService

RMI = 111
ANA = 222


@pytest_asyncio.fixture
async def users(tmp_path: Path) -> AsyncIterator[tuple[UserService, Database]]:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await apply_migrations(db.url)
    yield UserService(db.sessions, allowed_ids=frozenset({RMI})), db
    await db.dispose()


async def test_a_new_user_gets_a_record_the_moment_they_talk(
    users: tuple[UserService, Database],
) -> None:
    service, _ = users

    await service.record_sighting(Sighting(telegram_id=ANA, username="ana"))

    found = await service.get(ANA)
    assert found is not None
    assert found.username == "ana"


async def test_a_sighting_does_not_wipe_a_preference(users: tuple[UserService, Database]) -> None:
    # Every update records a sighting. If that overwrote settings, a user could never
    # keep a preference across two messages.
    service, _ = users
    await service.set_prefers_video(ANA, send_as_video=False)

    await service.record_sighting(Sighting(telegram_id=ANA, username="ana"))

    assert await service.prefers_video(ANA) is False


async def test_an_unknown_user_is_assumed_to_want_video(
    users: tuple[UserService, Database],
) -> None:
    service, _ = users

    assert await service.prefers_video(999) is True


async def test_a_preference_can_be_changed(users: tuple[UserService, Database]) -> None:
    service, _ = users
    await service.set_prefers_video(ANA, send_as_video=False)
    await service.set_prefers_video(ANA, send_as_video=True)

    assert await service.prefers_video(ANA) is True


async def test_setting_a_preference_creates_the_record(users: tuple[UserService, Database]) -> None:
    service, _ = users

    await service.set_prefers_video(ANA, send_as_video=False)

    assert await service.get(ANA) is not None


async def test_the_configured_allowlist_is_enough_on_its_own(
    users: tuple[UserService, Database],
) -> None:
    service, _ = users

    assert await service.may_use(RMI) is True


async def test_a_stranger_may_not_use_the_bot(users: tuple[UserService, Database]) -> None:
    service, _ = users

    assert await service.may_use(999) is False


async def test_an_admin_can_approve_somebody_persisted(users: tuple[UserService, Database]) -> None:
    service, _ = users
    await service.record_sighting(Sighting(telegram_id=ANA))

    await service.set_allowed(ANA, True)

    assert await service.may_use(ANA) is True


async def test_an_approval_can_be_withdrawn(users: tuple[UserService, Database]) -> None:
    service, _ = users
    await service.set_allowed(ANA, True)
    await service.set_allowed(ANA, False)

    assert await service.may_use(ANA) is False


async def test_an_approval_outlives_a_restart(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}"
    await apply_migrations(url)
    first = Database(url)
    await UserService(first.sessions, allowed_ids=frozenset()).set_allowed(ANA, True)
    await first.dispose()

    second = Database(url)
    try:
        assert await UserService(second.sessions, allowed_ids=frozenset()).may_use(ANA) is True
    finally:
        await second.dispose()


async def test_listing_users_is_ordered_so_an_admin_screen_is_stable(
    users: tuple[UserService, Database],
) -> None:
    service, _ = users
    for telegram_id in (333, 111, 222):
        await service.record_sighting(Sighting(telegram_id=telegram_id))

    found = [user.telegram_id for user in await service.list_users()]

    assert found == [111, 222, 333]
