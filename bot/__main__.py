"""Process entrypoint.

Container start and `python -m bot` both land here. The module has two jobs: turn a
broken environment into a short, actionable message on stderr and a non-zero exit code,
and then keep the bot running until somebody stops it.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, cast

from bot.app import build_application
from bot.config import ConfigurationError, Settings
from bot.logging_setup import configure_logging, get_logger
from bot.services.storage.db import Database
from bot.services.storage.migrations import apply_migrations

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from telegram.ext import Updater

    from bot.container import BotApplication

    #: What ``_poll`` does, named so a test can hand ``serve`` something else.
    Poll = Callable[[BotApplication], Awaitable[None]]
    #: What ``serve`` does, named so a test can hand ``main`` something else.
    Run = Callable[[Settings], None]

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_STARTUP_ERROR = 3

LOGGER_NAME = "bot"


def run_bot(settings: Settings) -> None:
    """Bring the database up to date, then poll until stopped."""
    asyncio.run(serve(settings))


async def _poll(application: BotApplication) -> None:
    """Poll the local Bot API server until the process is stopped.

    The updater is driven directly rather than through ``run_polling``, because
    ``run_polling`` is synchronous: it creates and closes its own event loop, which cannot
    be done from inside the one that owns the database. Here the same loop carries the
    migrations, the engine and the poller, so the engine is opened and closed in one place.
    """
    log = get_logger(LOGGER_NAME)
    updater = cast("Updater", application.updater)
    await application.initialize()
    try:
        await updater.start_polling(
            # Everything the bot answers: messages, commands and button presses. Leaving
            # this unset makes PTB fetch every update type the server knows about,
            # including the ones this bot has no handler for.
            allowed_updates=["message", "callback_query"],
            # A restart must not swallow what Telegram queued while the bot was down.
            drop_pending_updates=False,
        )
        log.info("bot.polling")
        # Nothing left to do here: the updater does the work. This waits so the process
        # stays up, and is cancelled by Ctrl-C or by a signal the container sends.
        await asyncio.Event().wait()
    finally:
        await updater.stop()
        await application.shutdown()


async def serve(settings: Settings, *, poll: Poll = _poll) -> None:
    """Own every resource for as long as the bot runs.

    The database is closed on the way out whether polling ended on its own or raised,
    because the restart that follows a bad token or an unreachable server is the case
    that leaks one.
    """
    database = Database(settings.database_url)
    try:
        await apply_migrations(settings.database_url)
        application = build_application(settings, database)
        await poll(application)
    finally:
        await database.dispose()


def main(argv: Sequence[str] | None = None, *, run: Run = run_bot) -> int:
    """Validate configuration, configure logging, and run the bot.

    ``run`` is a parameter so the tests here can exercise the real configuration path
    without polling, which by design never returns.
    """
    del argv  # no flags yet

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

    try:
        run(settings)
    except KeyboardInterrupt:
        # Ctrl-C, and `docker stop`. Normal, so not a failure exit.
        get_logger(LOGGER_NAME).info("bot.stopped")
    except Exception as exc:
        # One line naming the cause. The container restarts either way, and an operator
        # reading the logs at 3am needs the reason, not a traceback they have to decode.
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        get_logger(LOGGER_NAME).error("bot.startup_failed", error_type=type(exc).__name__)
        return EXIT_STARTUP_ERROR
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
