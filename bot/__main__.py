"""Process entrypoint.

Container start and `python -m bot` both land here. The job of this module is to
turn a broken environment into a short, actionable message on stderr and a non-zero
exit code, rather than letting a misconfigured deployment look like a healthy one.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from bot.config import ConfigurationError, Settings
from bot.logging_setup import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2

LOGGER_NAME = "bot"


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration, configure logging, and report readiness."""
    del argv  # no flags yet; the polling loop lands with the Telegram application

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_CONFIG_ERROR

    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
        secret_values=(
            settings.telegram_bot_token.get_secret_value(),
            settings.telegram_api_hash.get_secret_value(),
        ),
    )

    get_logger(LOGGER_NAME).info(
        "bot.startup",
        bot_api_url=settings.telegram_bot_api_url,
        local_mode=settings.telegram_bot_api_local,
        download_dir=str(settings.download_dir),
        max_upload_mb=settings.max_upload_mb,
        admins=len(settings.admin_user_ids),
        allowed=len(settings.allowed_user_ids),
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
