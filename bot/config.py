"""Environment-backed configuration.

Every deployment-specific value arrives through the environment, is validated once
at startup, and fails loudly with the name of the offending variable rather than a
traceback. Secrets are held as ``SecretStr`` so they cannot reach a log line by
accident.
"""

from __future__ import annotations

from pathlib import Path, PurePath, PurePosixPath
from typing import Annotated, Final

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

UserIds = Annotated[frozenset[int], NoDecode]

#: Paths bypass pydantic-settings' eager parsing so the raw text reaches the
#: validator below, before a native ``Path`` can hide its POSIX origin.
RootedPath = Annotated[Path, NoDecode]
OptionalRootedPath = Annotated[Path | None, NoDecode]

LOG_LEVELS: Final[frozenset[str]] = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

#: Hard ceiling of the local Telegram Bot API server, in megabytes. Anything above
#: this is rejected by Telegram itself, so configuration cannot raise it.
BOT_API_UPLOAD_LIMIT_MB: Final[int] = 2000

#: Decimal megabytes, not binary. Telegram states the limit as "2000 MB" without
#: naming the base; 10**6 is the only reading that is safe under either one.
BYTES_PER_MEGABYTE: Final[int] = 1_000_000


class ConfigurationError(RuntimeError):
    """The environment cannot produce a usable configuration."""


def _parse_user_ids(raw: object) -> object:
    """Accept ``1, 2, 3``, ``[1, 2, 3]``, an empty string, or a real collection."""
    if not isinstance(raw, str):
        return raw
    cleaned = raw.strip().removeprefix("[").removesuffix("]").strip()
    if not cleaned:
        return frozenset()
    try:
        return frozenset(int(part.strip()) for part in cleaned.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError(
            "expected a comma-separated list of Telegram user IDs, for example 123456789,987654321"
        ) from exc


def _is_rooted(value: str) -> bool:
    """True when the path is anchored to a root, on either platform.

    The container path is POSIX while development often happens on Windows, and
    ``Path("/data/downloads")`` is drive-less there. Testing for a non-empty anchor
    accepts both spellings and still rejects genuinely relative paths.
    """
    return bool(PurePath(value).anchor) or bool(PurePosixPath(value).anchor)


class Settings(BaseSettings):
    """Validated view over the process environment and the optional ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Telegram credentials ------------------------------------------- #
    telegram_bot_token: SecretStr
    telegram_api_id: int
    telegram_api_hash: SecretStr
    telegram_bot_api_url: str = "http://telegram-bot-api:8081/bot"
    telegram_bot_api_local: bool = True

    # -- Access control --------------------------------------------------- #
    admin_user_ids: UserIds
    allowed_user_ids: UserIds = frozenset()

    # -- Storage ---------------------------------------------------------- #
    database_url: str = "sqlite+aiosqlite:////data/bot.db"
    download_dir: RootedPath = Path("/data/downloads")
    bot_api_data_dir: RootedPath = Path("/var/lib/telegram-bot-api")
    youtube_cookies_file: OptionalRootedPath = None

    # -- Limits ----------------------------------------------------------- #
    max_upload_mb: int = Field(default=BOT_API_UPLOAD_LIMIT_MB, ge=1, le=2000)
    max_playlist_items: int = Field(default=50, ge=1, le=500)
    max_concurrent_downloads: int = Field(default=1, ge=1, le=16)
    rate_limit_per_hour: int = Field(default=10, ge=1, le=1000)

    # -- Housekeeping ----------------------------------------------------- #
    allow_unknown_sizes: bool = False
    orphan_ttl_seconds: int = Field(default=6 * 3600, ge=60)
    janitor_interval_seconds: int = Field(default=900, ge=60)
    disk_free_threshold_gb: float = Field(default=5.0, ge=0.0)

    # -- Observability ---------------------------------------------------- #
    log_level: str = "INFO"
    log_json: bool = True
    metrics_host: str = "0.0.0.0"  # noqa: S104 - bound inside the container network
    metrics_port: int = Field(default=9090, ge=1, le=65535)

    # -- Validators ------------------------------------------------------- #

    @field_validator("admin_user_ids", "allowed_user_ids", mode="before")
    @classmethod
    def _validate_user_ids(cls, raw: object) -> object:
        return _parse_user_ids(raw)

    @field_validator("admin_user_ids")
    @classmethod
    def _require_an_admin(cls, value: frozenset[int]) -> frozenset[int]:
        if not value:
            raise ValueError(
                "at least one admin is required, otherwise access requests can never be approved"
            )
        return value

    @field_validator("telegram_bot_api_url")
    @classmethod
    def _validate_bot_api_url(cls, value: str, info: ValidationInfo) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an absolute http:// or https:// URL")
        token = info.data.get("telegram_bot_token")
        if isinstance(token, SecretStr) and token.get_secret_value() in value:
            raise ValueError("must not contain the bot token; python-telegram-bot appends it")
        return value

    @field_validator("download_dir", "bot_api_data_dir", mode="before")
    @classmethod
    def _validate_absolute_dir(cls, value: object) -> object:
        """Check the raw text, before pydantic turns it into a native Path.

        On Windows ``Path("/data/downloads")`` becomes a drive-less ``\\data\\downloads``
        that fails ``is_absolute()``, so validating the string keeps the container's
        POSIX paths usable during Windows development.
        """
        if value is None:
            return value
        text = value if isinstance(value, str) else str(value)
        if not _is_rooted(text):
            raise ValueError(f"must be an absolute path, got {text}")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalised = value.strip().upper()
        if normalised not in LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(sorted(LOG_LEVELS))}, got {value!r}")
        return normalised

    # -- Derived values --------------------------------------------------- #

    @property
    def max_upload_bytes(self) -> int:
        """The largest file the Bot API server will accept, in bytes."""
        return self.max_upload_mb * BYTES_PER_MEGABYTE

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    def is_allowed(self, user_id: int) -> bool:
        """Admins are always allowed; everyone else needs to be on the list."""
        return self.is_admin(user_id) or user_id in self.allowed_user_ids

    # -- Startup ---------------------------------------------------------- #

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings, translating validation failures into operator guidance.

        The contract requires startup to name the environment variable at fault, not
        to raise a pydantic traceback at whoever deployed the bot.
        """
        try:
            return cls()
        except ValidationError as exc:
            raise ConfigurationError(cls._render_errors(exc)) from exc

    @classmethod
    def _render_errors(cls, exc: ValidationError) -> str:
        lines = [
            f"Invalid configuration ({len(exc.errors())} problem(s) found):",
        ]
        for error in exc.errors():
            field = str(error["loc"][0]) if error["loc"] else "settings"
            lines.append(f"  - {cls.env_name(field)}: {error['msg']}")
        lines.append("  See .env.example for the full list of accepted values.")
        return "\n".join(lines)

    @staticmethod
    def env_name(field: str) -> str:
        """Map a settings field to the environment variable that feeds it."""
        info = Settings.model_fields.get(field)
        alias = info.validation_alias if info is not None else None
        name = alias if isinstance(alias, str) else field
        return name.upper()
