"""Specification for how the bot is put together.

Assembly is the one piece every other test stands on. If the container is not where the
handlers look for it, or the local Bot API URL is ignored, nothing else matters, and
neither failure shows up until the bot is deployed and silently answering nobody.

These tests build the real application. Nothing polls, because polling belongs to
``start``, so an assembled bot can be driven one update at a time exactly as a live one
is driven.

Logging is checked through a recording logger rather than through captured output. What
these tests care about is which fact reaches the log and at what severity; how a line is
rendered, and whether a secret is scrubbed out of it, belongs to the logging tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
import pytest_asyncio
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import CallbackContext
from tests.fakes import FakeVideos, a_description
from tests.telegram_stub import (
    DEFAULT_CHAT_ID,
    RecordingTelegram,
    a_callback_update,
    a_text_update,
    wire,
)

from bot import app as app_module
from bot.app import GENERIC_APOLOGY, build_application, build_services, on_application_error
from bot.config import Settings
from bot.container import SERVICES_KEY, Services
from bot.keyboards.reply import SINGLE_VIDEO_LABEL
from bot.services.storage.db import Database
from bot.services.storage.migrations import apply_migrations

if TYPE_CHECKING:
    from bot.container import BotApplication, BotContext

TOKEN = "123456789:AAF-fake-token-for-tests"
ADMIN_ID = 7
STRANGER_ID = 999_001
URL = "https://youtu.be/abcdefghijk"


@dataclass(slots=True)
class Call:
    """One line the handler decided to log."""

    level: str
    event: str
    fields: dict[str, Any]


@dataclass(slots=True)
class RecordingLog:
    """Stands in for the bot's logger.

    The handler is given this instead of a real logger so a test can say what it wrote.
    Asserting on rendered text instead would mostly be re-testing the renderer.
    """

    calls: list[Call] = field(default_factory=list)

    def warning(self, event: str, **fields: Any) -> None:
        self.calls.append(Call("warning", event, fields))

    def exception(self, event: str, **fields: Any) -> None:
        self.calls.append(Call("exception", event, fields))

    @property
    def last(self) -> Call:
        return self.calls[-1]


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> RecordingLog:
    log = RecordingLog()
    monkeypatch.setattr(app_module, "get_logger", lambda _name: log)
    return log


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    """A real SQLite file with the real migrations on it."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    await apply_migrations(db.url)
    try:
        yield db
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def ready(
    settings: Settings,
    database: Database,
    telegram: RecordingTelegram,
) -> AsyncIterator[BotApplication]:
    """An initialised application, so a real update can be dispatched through it."""
    application = build_application(settings, database)
    await application.initialize()
    try:
        yield application
    finally:
        await application.shutdown()


@pytest_asyncio.fixture
async def assembled(
    settings: Settings,
    database: Database,
    telegram: RecordingTelegram,
) -> AsyncIterator[Services]:
    """The container the shipped application hands its handlers."""
    application = build_application(settings, database)
    await application.initialize()
    try:
        yield cast("Services", application.bot_data[SERVICES_KEY])
    finally:
        await application.shutdown()


def a_context(ready: BotApplication, error: Exception | None = None) -> BotContext:
    """The context python-telegram-bot builds when an update fails."""
    if error is None:
        return CallbackContext(application=ready)
    return CallbackContext.from_error(update=Update(update_id=0), error=error, application=ready)


def test_the_bot_is_pointed_at_the_local_bot_api(settings: Settings) -> None:
    # The configured URL already ends in "/bot", and the token is appended to it. Checking
    # the whole URL is what catches a token silently left off, which sends every request
    # to a 404 the operator cannot see from inside the container.
    application = build_application(settings, Database("sqlite+aiosqlite:///:memory:"))
    bot = application.bot

    assert bot.base_url == f"http://telegram-bot-api:8081/bot{TOKEN}"
    # Local mode is what makes getFile return a path on the Bot API server's disk instead
    # of handing this bot a file to pull back over the network.
    assert bot.local_mode is True


async def test_the_container_is_where_the_handlers_look_for_it(assembled: Services) -> None:
    assert isinstance(assembled, Services)
    # The real user service on the real database. Assembly that handed the handlers a
    # stub would pass every other test in this suite and remember nothing.
    assert await assembled.users.get(ADMIN_ID) is None


async def test_admins_are_allowed_without_being_written_down(
    settings: Settings, database: Database
) -> None:
    # An admin is named in the environment and has no row in the database. Reading the
    # database alone would lock every admin out of the bot that only admins can fix.
    application = build_application(settings, database)
    services = build_services(application, settings, database)

    assert await services.users.may_use(ADMIN_ID) is True
    assert await services.users.may_use(STRANGER_ID) is False


async def test_a_link_reaches_the_flow_and_not_the_catch_all(
    ready: BotApplication, telegram: RecordingTelegram
) -> None:
    # The registration order is the fragile part: the catch-all matches every text
    # message, so an order that puts it first disables the bot without failing anything
    # else. Driving real updates is the only way to notice.
    services = cast("Services", ready.bot_data[SERVICES_KEY])
    ready.bot_data[SERVICES_KEY] = replace(services, videos=FakeVideos(a_description()))

    for update in (
        a_text_update(SINGLE_VIDEO_LABEL, user_id=ADMIN_ID),
        a_text_update(URL, user_id=ADMIN_ID),
    ):
        await ready.process_update(wire(update, ready.bot))

    # The quality menu, not the "tap one of the buttons" nudge the catch-all would give.
    assert "A Video" in telegram.said[-1]
    assert "1080p" in telegram.buttons_shown[-1]
    assert cast("Services", ready.bot_data[SERVICES_KEY]).flow.recall(DEFAULT_CHAT_ID) is not None


async def test_a_failure_is_apologised_for_without_naming_the_cause(
    ready: BotApplication, telegram: RecordingTelegram, logged: RecordingLog
) -> None:
    # The last line of defence. Anything a handler did not catch arrives here, and an
    # exception text names files, queries and code, none of which is the user's business.
    await on_application_error(
        wire(a_text_update(SINGLE_VIDEO_LABEL, user_id=ADMIN_ID), ready.bot),
        a_context(ready, RuntimeError("psycopg2://bot:hunter2@db/bot is unreachable")),
    )

    said = " ".join(telegram.said)
    assert "hunter2" not in said
    assert GENERIC_APOLOGY in said


async def test_the_failure_itself_is_kept_for_the_operator(
    ready: BotApplication, logged: RecordingLog
) -> None:
    # The apology is only useful if the real reason reached the log. Swallowing an
    # exception is how one bug survives a deployment for a month.
    error = RuntimeError("the actual cause")

    await on_application_error(
        wire(a_text_update(SINGLE_VIDEO_LABEL, user_id=ADMIN_ID), ready.bot),
        a_context(ready, error),
    )

    # The exception is passed along rather than picked up from the ambient exception
    # state: this handler runs from a task, not from inside an ``except`` block, where
    # there would be nothing there to find.
    assert logged.last.event == "unhandled error while dispatching an update"
    assert logged.last.level == "exception"
    assert logged.last.fields["exc_info"] is error
    assert logged.last.fields["error_type"] == "RuntimeError"
    assert logged.last.fields["update_id"] == 1


async def test_a_failed_button_stops_spinning(
    ready: BotApplication, telegram: RecordingTelegram
) -> None:
    # A callback query left unanswered leaves the client spinning on the button until it
    # times out, which reads as "the bot is broken" even when the handler was fine.
    await on_application_error(
        wire(a_callback_update("q:480p", user_id=ADMIN_ID), ready.bot),
        a_context(ready, RuntimeError("boom")),
    )

    assert "answerCallbackQuery" in telegram.endpoints


async def test_a_button_press_is_not_apologised_for(
    ready: BotApplication, telegram: RecordingTelegram, logged: RecordingLog
) -> None:
    # The user pressed a button; they are not to be told the bot had a bad time. The
    # button stopping is the whole answer, and a message would be noise on top of it.
    await on_application_error(
        wire(a_callback_update("q:480p", user_id=ADMIN_ID), ready.bot),
        a_context(ready, RuntimeError("boom")),
    )

    assert telegram.said == []


async def test_an_update_with_nothing_to_reply_to_is_still_logged(
    ready: BotApplication, telegram: RecordingTelegram, logged: RecordingLog
) -> None:
    # Telegram sends updates this bot has no handler for: an inline query, a reaction, a
    # poll answer. There is no chat to apologise in, which makes the log the only record.
    error = RuntimeError("unanswerable")

    await on_application_error(Update(update_id=99), a_context(ready, error))

    assert logged.last.fields["exc_info"] is error
    assert logged.last.fields["update_id"] == 99
    assert telegram.said == []


async def test_a_failure_with_no_error_on_the_context_is_still_handled(
    ready: BotApplication, telegram: RecordingTelegram, logged: RecordingLog
) -> None:
    # Not a path the library takes: it builds the context from the exception. Handled
    # anyway, because a handler that raises while reporting a failure turns one broken
    # update into a loop of them.
    await on_application_error(
        wire(a_text_update(SINGLE_VIDEO_LABEL, user_id=ADMIN_ID), ready.bot),
        a_context(ready),
    )

    assert logged.last.level == "warning"
    assert logged.last.fields["error_type"] is None
    assert GENERIC_APOLOGY in " ".join(telegram.said)


async def test_telegram_refusing_a_request_is_not_a_bug(
    ready: BotApplication, logged: RecordingLog
) -> None:
    # A flood limit or a deleted message arrives as an exception like any other. A
    # traceback for each one would bury the failures that are actually ours.
    await on_application_error(
        wire(a_text_update(SINGLE_VIDEO_LABEL, user_id=ADMIN_ID), ready.bot),
        a_context(ready, BadRequest("message to edit not found")),
    )

    assert logged.last.level == "warning"
    assert logged.last.event == "telegram refused a request"
    assert "message to edit not found" in str(logged.last.fields["detail"])
    assert "exc_info" not in logged.last.fields


async def test_a_failure_to_answer_the_button_is_still_recorded(
    ready: BotApplication, telegram: RecordingTelegram, logged: RecordingLog
) -> None:
    # Telegram refusing the answerCallbackQuery too, because the query is older than the
    # two minutes it stays answerable. Silently giving up here means a client spins until
    # it gives up itself, with nothing in the log to explain it.
    telegram.refuse("answerCallbackQuery")

    await on_application_error(
        wire(a_callback_update("q:480p", user_id=ADMIN_ID), ready.bot),
        a_context(ready, RuntimeError("boom")),
    )

    assert "could not answer a callback query" in str(logged.last.fields["detail"])


async def test_a_failure_to_apologise_is_still_recorded(
    ready: BotApplication, telegram: RecordingTelegram, logged: RecordingLog
) -> None:
    # The user blocked the bot between tapping and the failure. Nothing can be said, and
    # the attempt has to be visible so the silence is not read as success.
    telegram.refuse("sendMessage")

    await on_application_error(
        wire(a_text_update(SINGLE_VIDEO_LABEL, user_id=ADMIN_ID), ready.bot),
        a_context(ready, RuntimeError("boom")),
    )

    assert "could not apologise" in str(logged.last.fields["detail"])
