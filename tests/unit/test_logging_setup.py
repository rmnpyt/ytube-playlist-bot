"""Specification for structured logging.

The contract forbids secrets reaching stdout, stderr or the database, and requires
that a deployment mistake is diagnosable from the logs. These tests fix both.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest
import structlog

from bot.logging_setup import (
    REDACTED,
    configure_logging,
    get_logger,
    redact_secrets,
)

TOKEN = "123456789:AAF-super-secret-bot-token"
API_HASH = "0123456789abcdef0123456789abcdef"
SECRETS = frozenset({TOKEN, API_HASH})


# --------------------------------------------------------------------------- #
# Key-based redaction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key",
    [
        "telegram_bot_token",
        "api_hash",
        "telegram_api_hash",
        "password",
        "authorization",
        "cookie",
        "cookies_file",
        "credentials",
        "private_key",
    ],
)
def test_secret_looking_keys_are_masked(key: str) -> None:
    redacted = redact_secrets({key: "whatever"}, SECRETS)

    assert redacted[key] == REDACTED


def test_key_matching_is_case_insensitive() -> None:
    redacted = redact_secrets({"Authorization": "Bearer abc"}, SECRETS)

    assert redacted["Authorization"] == REDACTED


def test_ordinary_keys_survive_untouched() -> None:
    event = {"event": "download.started", "video_id": "abc123", "size_bytes": 42}

    assert redact_secrets(event, SECRETS) == event


def test_the_original_event_is_not_mutated() -> None:
    event = {"telegram_bot_token": TOKEN}

    redact_secrets(event, SECRETS)

    assert event["telegram_bot_token"] == TOKEN


# --------------------------------------------------------------------------- #
# Value-based redaction
# --------------------------------------------------------------------------- #


def test_known_secret_values_are_masked_under_any_key() -> None:
    redacted = redact_secrets({"note": f"token is {TOKEN}"}, SECRETS)

    assert redacted["note"] == f"token is {REDACTED}"
    assert TOKEN not in str(redacted)


def test_short_secret_values_do_not_destroy_the_message() -> None:
    """Masking a two-character value everywhere would redact the whole log."""
    redacted = redact_secrets({"note": "all good"}, frozenset({"al"}))

    assert redacted["note"] == "all good"


def test_nested_structures_are_cleaned() -> None:
    redacted = redact_secrets(
        {"context": {"api_hash": API_HASH, "items": [{"password": "hunter2"}]}},
        SECRETS,
    )

    assert redacted["context"]["api_hash"] == REDACTED
    assert redacted["context"]["items"][0]["password"] == REDACTED


def test_non_string_leaves_are_passed_through() -> None:
    redacted = redact_secrets({"count": 7, "ratio": 0.5, "ok": True, "missing": None}, SECRETS)

    assert redacted == {"count": 7, "ratio": 0.5, "ok": True, "missing": None}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_configure_logging_sets_the_stdlib_level() -> None:
    configure_logging(level="WARNING", json_output=True, secret_values=SECRETS)

    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_is_idempotent() -> None:
    configure_logging(level="INFO", json_output=True, secret_values=SECRETS)
    configure_logging(level="INFO", json_output=True, secret_values=SECRETS)

    assert len(logging.getLogger().handlers) == 1


def test_get_logger_returns_a_usable_logger() -> None:
    configure_logging(level="INFO", json_output=True, secret_values=SECRETS)

    logger = get_logger(__name__)

    assert callable(logger.info)
    assert callable(logger.bind)


@pytest.mark.parametrize(
    ("json_output", "renderer"),
    [
        (True, structlog.processors.JSONRenderer),
        (False, structlog.dev.ConsoleRenderer),
    ],
)
def test_renderer_choice_follows_the_flag(json_output: bool, renderer: type[Any]) -> None:
    configure_logging(level="INFO", json_output=json_output, secret_values=SECRETS)

    processors = structlog.get_config()["processors"]

    assert isinstance(processors[-1], renderer)


# --------------------------------------------------------------------------- #
# End to end: a real log line must not contain a secret
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("json_output", [True, False])
def test_emitted_log_lines_never_contain_a_secret(
    capsys: pytest.CaptureFixture[str], json_output: bool
) -> None:
    configure_logging(level="DEBUG", json_output=json_output, secret_values=SECRETS)

    get_logger("bot.test").info(
        "bot.upstream",
        telegram_bot_token=TOKEN,
        telegram_api_hash=API_HASH,
        detail=f"connecting with {TOKEN}",
    )

    captured = capsys.readouterr()
    assert TOKEN not in captured.out
    assert API_HASH not in captured.out
    assert REDACTED in captured.out


def test_exceptions_are_rendered_with_their_type(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="DEBUG", json_output=False, secret_values=SECRETS)

    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("bot.test").exception("download.failed")

    assert "ValueError" in capsys.readouterr().out


def test_level_filtering_respects_the_threshold(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="ERROR", json_output=False, secret_values=SECRETS)

    get_logger("bot.test").debug("noisy.event")

    assert "noisy.event" not in capsys.readouterr().out


@pytest.fixture(autouse=True)
def _restore_logging() -> Any:
    yield
    configure_logging(level="INFO", json_output=True, secret_values=frozenset())
    logging.getLogger().handlers.clear()
    structlog.reset_defaults()
    sys.stdout.flush()
