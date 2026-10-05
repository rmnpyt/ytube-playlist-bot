"""Tests for the access request flow."""

from __future__ import annotations

from tests.fakes import FakeUsers


async def test_fake_users_allows_by_default() -> None:
    svc = FakeUsers(allowed=True)
    assert await svc.may_use(123)


async def test_fake_users_denies_by_default() -> None:
    svc = FakeUsers(allowed=False)
    assert not await svc.may_use(123)


async def test_fake_users_request_access_creates_pending() -> None:
    svc = FakeUsers(allowed=False)
    created = await svc.request_access(456, "testuser")
    assert created
    assert not await svc.may_use(456)  # still not allowed until approved

    pending = await svc.list_pending_requests()
    assert len(pending) == 1
    assert pending[0].telegram_id == 456


async def test_fake_users_duplicate_request_returns_false() -> None:
    svc = FakeUsers(allowed=False)
    await svc.request_access(456, "testuser")
    created = await svc.request_access(456, "testuser")
    assert not created


async def test_fake_users_approve_makes_allowed() -> None:
    svc = FakeUsers(allowed=False)
    await svc.request_access(456, "testuser")
    approved = await svc.approve_request(456)
    assert approved
    assert await svc.may_use(456)
    assert len(await svc.list_pending_requests()) == 0


async def test_fake_users_approve_unknown_returns_false() -> None:
    svc = FakeUsers(allowed=False)
    approved = await svc.approve_request(999)
    assert not approved


async def test_fake_users_deny_removes_pending() -> None:
    svc = FakeUsers(allowed=False)
    await svc.request_access(456, "testuser")
    denied = await svc.deny_request(456)
    assert denied
    assert not await svc.may_use(456)
    assert len(await svc.list_pending_requests()) == 0


async def test_fake_users_deny_unknown_returns_false() -> None:
    svc = FakeUsers(allowed=False)
    denied = await svc.deny_request(999)
    assert not denied


async def test_fake_users_already_allowed_cannot_request() -> None:
    svc = FakeUsers(allowed=True)
    created = await svc.request_access(456, "testuser")
    assert not created


async def test_fake_users_override_works() -> None:
    svc = FakeUsers(allowed=False)
    await svc.set_allowed(789, True)
    assert await svc.may_use(789)
