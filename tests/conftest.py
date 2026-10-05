"""Shared pytest fixtures and environment isolation."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from telegram.ext import ApplicationBuilder

from bot.config import Settings
from bot.container import SERVICES_KEY, Services
from bot.handlers import menu, single
from bot.services.flow import Flow
from tests.fakes import FakeDelivery, FakeUsers, FakeVideos
from tests.telegram_stub import RecordingTelegram

if TYPE_CHECKING:
    from bot.container import BotApplication

_ENV_PREFIXES = ("TELEGRAM_", "ADMIN_", "ALLOWED_", "MAX_", "ALLOW_", "LOG_")
_SETTING_ENV_KEYS = frozenset(name.upper() for name in Settings.model_fields)

TEST_TOKEN = "123456789:AAF-fake-token-for-tests"


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Run every test in a clean, empty working directory.

    Settings reads both the process environment and a ``.env`` file in the current
    directory. Both are neutralised here so a developer's own ``.env`` can never
    influence, or be broken by, the test suite.
    """
    for key in _SETTING_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def minimal_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A complete, valid set of required environment values."""
    env = {
        "TELEGRAM_BOT_TOKEN": TEST_TOKEN,
        "TELEGRAM_API_ID": "424242",
        "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
        "ADMIN_USER_IDS": "7",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


@pytest.fixture
def settings(minimal_env: dict[str, str]) -> Settings:
    return Settings()


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> RecordingTelegram:
    """Every outbound Telegram call, recorded instead of sent."""
    recorder = RecordingTelegram()
    recorder.install(monkeypatch)
    return recorder


@pytest.fixture
def services(settings: Settings) -> Services:
    """The container handlers are given, with fakes in place of the outside world."""
    return Services(
        settings=settings,
        users=FakeUsers(),
        videos=FakeVideos(),
        delivery=FakeDelivery(),
        flow=Flow(),
    )


@pytest.fixture
async def application(
    services: Services,
    telegram: RecordingTelegram,
) -> AsyncIterator[BotApplication]:
    """A real application, with the real registration order the bot ships.

    Initialised but never started: ``process_update`` refuses to dispatch until
    ``initialize`` has run, and initialising is what wires up the dispatcher and the job
    queue. Nothing polls, because ``start`` is what polls, and no test needs an updater
    fetching updates from the internet.

    ``telegram`` is a dependency rather than a convenience: initialising asks the Telegram
    API who the bot is, so the stub has to be in place before this fixture runs. Asking
    for it here is what guarantees the order, whatever order the test lists its own
    arguments in.
    """
    # The builder has no way to be told which bot type to fill in, so it answers with its own
    # default. Everything below is then checked against the alias the handlers are written
    # against, which is the part that matters: the registration calls are verified.
    app = cast("BotApplication", ApplicationBuilder().token(TEST_TOKEN).build())
    app.bot_data[SERVICES_KEY] = services
    menu.register_menu(app)
    single.register(app)
    menu.register_fallback(app)
    await app.initialize()
    try:
        yield app
    finally:
        await app.shutdown()
