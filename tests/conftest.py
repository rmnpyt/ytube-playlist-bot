"""Shared pytest fixtures and environment isolation."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from bot.config import Settings

_ENV_PREFIXES = ("TELEGRAM_", "ADMIN_", "ALLOWED_", "MAX_", "ALLOW_", "LOG_")
_SETTING_ENV_KEYS = frozenset(name.upper() for name in Settings.model_fields)


@pytest.fixture(autouse=True)
def isolate_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
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
        "TELEGRAM_BOT_TOKEN": "123456789:AAF-fake-token-for-tests",
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
