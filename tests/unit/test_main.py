"""Specification for the process entrypoint.

Startup is the one moment the bot can tell a misconfigured deployment what is
wrong, before a user ever presses /start. These tests pin that behaviour.
"""

from __future__ import annotations

import pytest

from bot.__main__ import EXIT_CONFIG_ERROR, EXIT_OK, main


def test_valid_environment_exits_successfully(
    minimal_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([]) == EXIT_OK


def test_startup_logs_are_emitted(
    minimal_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    main([])

    captured = capsys.readouterr()
    assert "bot.startup" in captured.out


def test_configuration_errors_are_reported_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "42")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ADMIN_USER_IDS", "7")

    assert main([]) == EXIT_CONFIG_ERROR
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_configuration_errors_do_not_leak_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "42")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ADMIN_USER_IDS", "7")

    main([])

    assert "Traceback" not in capsys.readouterr().err


def test_startup_never_prints_the_token(
    minimal_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    main([])

    assert minimal_env["TELEGRAM_BOT_TOKEN"] not in capsys.readouterr().out
