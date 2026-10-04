"""Specification for the environment-backed configuration.

These tests were written before `bot/config.py`. Every test here describes
behaviour the contract in docs/CONTRACT.md requires: fast failure on missing
secrets, and never exceeding what the Telegram Bot API server can accept.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bot.config import Settings

REQUIRED = {
    "TELEGRAM_BOT_TOKEN": "123456789:AAF-fake-token-for-tests",
    "TELEGRAM_API_ID": "424242",
    "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
    "ADMIN_USER_IDS": "7",
}


def _valid_env(**overrides: str) -> dict[str, str]:
    env = {**REQUIRED, **overrides}
    return {key: value for key, value in env.items() if value != ""}


# --------------------------------------------------------------------------- #
# Required values and fast failure
# --------------------------------------------------------------------------- #


def test_minimal_environment_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)

    assert Settings().telegram_bot_token.get_secret_value() == REQUIRED["TELEGRAM_BOT_TOKEN"]


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_variable_fails_fast(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    env = _valid_env()
    env.pop(missing)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    assert missing.lower() in str(excinfo.value).lower()


def test_error_message_names_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "424242")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ADMIN_USER_IDS", "7")

    with pytest.raises(ValidationError) as excinfo:
        Settings()

    assert "TELEGRAM_BOT_TOKEN" in str(excinfo.value)


def test_non_numeric_api_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _valid_env(TELEGRAM_API_ID="not-a-number")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError):
        Settings()


def test_unknown_environment_variables_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SOME_UNRELATED_VARIABLE", "noise")

    assert Settings().log_level == "INFO"


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


def test_defaults_match_the_contract(settings: Settings) -> None:
    assert settings.telegram_bot_api_url == "http://telegram-bot-api:8081/bot"
    assert settings.telegram_bot_api_local is True
    assert settings.max_upload_mb == 2000
    assert settings.max_playlist_items == 50
    assert settings.max_concurrent_downloads == 1
    assert settings.allow_unknown_sizes is False
    assert settings.allowed_user_ids == frozenset()
    assert settings.youtube_cookies_file is None


# --------------------------------------------------------------------------- #
# The 2 GB ceiling
# --------------------------------------------------------------------------- #


def test_max_upload_bytes_uses_decimal_megabytes(settings: Settings) -> None:
    assert settings.max_upload_bytes == 2_000_000_000


def test_max_upload_bytes_stays_under_the_server_hard_limit(
    settings: Settings,
) -> None:
    """Telegram documents the ceiling as 2000 MB without stating the base.

    Using decimal megabytes is the only reading that is safe under either
    interpretation, so the bot can never produce a file the server rejects.
    """
    assert settings.max_upload_bytes <= 2000 * 1024 * 1024


def test_ceiling_above_the_bot_api_limit_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _valid_env(MAX_UPLOAD_MB="4000").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="max_upload_mb"):
        Settings()


def test_ceiling_below_one_megabyte_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(MAX_UPLOAD_MB="0").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="max_upload_mb"):
        Settings()


def test_playlist_limit_must_be_within_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(MAX_PLAYLIST_ITEMS="0").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="max_playlist_items"):
        Settings()


def test_concurrency_limit_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(MAX_CONCURRENT_DOWNLOADS="0").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="max_concurrent_downloads"):
        Settings()


# --------------------------------------------------------------------------- #
# User ID collections
# --------------------------------------------------------------------------- #


def test_admin_ids_accept_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(ADMIN_USER_IDS="7, 42 ,99").items():
        monkeypatch.setenv(key, value)

    assert Settings().admin_user_ids == frozenset({7, 42, 99})


def test_allowed_ids_accept_a_json_style_list(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(ALLOWED_USER_IDS="[1, 2, 3]").items():
        monkeypatch.setenv(key, value)

    assert Settings().allowed_user_ids == frozenset({1, 2, 3})


def test_an_empty_allowlist_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(ALLOWED_USER_IDS="").items():
        monkeypatch.setenv(key, value)

    assert Settings().allowed_user_ids == frozenset()


def test_at_least_one_admin_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(ADMIN_USER_IDS="").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="admin_user_ids"):
        Settings()


def test_non_numeric_user_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(ADMIN_USER_IDS="me").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="admin_user_ids"):
        Settings()


def test_admin_is_implicitly_allowed(settings: Settings) -> None:
    assert settings.is_allowed(settings.admin_user_ids.copy().pop()) is True


def test_unknown_user_is_not_allowed(settings: Settings) -> None:
    assert settings.is_allowed(999_999) is False


# --------------------------------------------------------------------------- #
# Bot API endpoint
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", ["telegram-bot-api:8081/bot", "ftp://host/bot"])
def test_bot_api_url_must_be_absolute_http(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    for key, value in _valid_env(TELEGRAM_BOT_API_URL=url).items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="telegram_bot_api_url"):
        Settings()


def test_bot_api_url_must_not_embed_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = REQUIRED["TELEGRAM_BOT_TOKEN"]
    url = f"http://telegram-bot-api:8081/bot{token}"
    for key, value in _valid_env(TELEGRAM_BOT_API_URL=url).items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="telegram_bot_api_url"):
        Settings()


def test_https_bot_api_url_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://bot.example.com/bot"
    for key, value in _valid_env(TELEGRAM_BOT_API_URL=url).items():
        monkeypatch.setenv(key, value)

    assert Settings().telegram_bot_api_url == url


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def test_download_dir_must_be_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(DOWNLOAD_DIR="downloads").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="download_dir"):
        Settings()


def test_download_dir_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/bot")
    for key, value in _valid_env(DOWNLOAD_DIR="/home/bot/dl/").items():
        monkeypatch.setenv(key, value)

    assert Settings().download_dir.is_absolute()


# --------------------------------------------------------------------------- #
# Secrets never leak
# --------------------------------------------------------------------------- #


def test_repr_masks_every_secret(settings: Settings) -> None:
    rendered = repr(settings)

    assert REQUIRED["TELEGRAM_BOT_TOKEN"] not in rendered
    assert REQUIRED["TELEGRAM_API_HASH"] not in rendered
    assert "**********" in rendered


def test_secret_values_are_not_in_the_dump(settings: Settings) -> None:
    dumped = settings.model_dump()

    assert "**********" in str(dumped["telegram_bot_token"])
    assert REQUIRED["TELEGRAM_API_HASH"] not in str(dumped)


# --------------------------------------------------------------------------- #
# Logging and misc
# --------------------------------------------------------------------------- #


def test_log_level_is_case_insensitive_and_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _valid_env(LOG_LEVEL="debug").items():
        monkeypatch.setenv(key, value)
    assert Settings().log_level == "DEBUG"

    for key, value in _valid_env(LOG_LEVEL="LOUD").items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError, match="log_level"):
        Settings()


def test_orphan_ttl_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _valid_env(ORPHAN_TTL_SECONDS="0").items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError, match="orphan_ttl_seconds"):
        Settings()


def test_settings_load_from_a_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in REQUIRED.items()),
        encoding="utf-8",
    )

    loaded = Settings()

    assert loaded.admin_user_ids == frozenset({7})
