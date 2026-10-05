"""Specification for the process entrypoint.

Startup is the one moment the bot can tell a misconfigured deployment what is
wrong, before a user ever presses /start. These tests pin that behaviour.

``main`` takes whatever actually runs the bot as an argument. Polling never returns on
its own, so a test that called it for real would hang rather than fail, and the seam lets
every test here exercise the real configuration path without a network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy import inspect

from bot import __main__ as main_module
from bot.__main__ import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_STARTUP_ERROR,
    _poll,
    main,
    run_bot,
    serve,
)
from bot.config import Settings
from bot.container import SERVICES_KEY, Services
from bot.services.janitor import Janitor, SweepResult
from bot.services.storage.db import Database

if TYPE_CHECKING:
    from bot.container import BotApplication


def do_nothing(settings: Settings) -> None:
    """A runner that starts no bot, for tests about everything that happens before it."""
    del settings


def test_valid_environment_exits_successfully(minimal_env: dict[str, str]) -> None:
    assert main([], run=do_nothing) == EXIT_OK


def test_startup_logs_are_emitted(
    minimal_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    main([], run=do_nothing)

    captured = capsys.readouterr()
    assert "bot.startup" in captured.out


def test_configuration_errors_are_reported_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "42")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ADMIN_USER_IDS", "7")

    assert main([], run=do_nothing) == EXIT_CONFIG_ERROR
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_configuration_errors_do_not_leak_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "42")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("ADMIN_USER_IDS", "7")

    main([], run=do_nothing)

    assert "Traceback" not in capsys.readouterr().err


def test_startup_never_prints_the_token(
    minimal_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    main([], run=do_nothing)

    assert minimal_env["TELEGRAM_BOT_TOKEN"] not in capsys.readouterr().out


def test_a_valid_environment_hands_its_settings_to_the_runner(
    minimal_env: dict[str, str],
) -> None:
    # Configuration that is validated and then thrown away is how a deployment ends up
    # running against the wrong database URL with nothing complaining.
    handed_to: list[Settings] = []

    assert main([], run=handed_to.append) == EXIT_OK

    assert [one.database_url for one in handed_to] == ["sqlite+aiosqlite:////data/bot.db"]


def test_a_runner_that_fails_is_reported_without_a_traceback(
    minimal_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    # A bot that cannot reach its own Bot API server must say so in one line. The restart
    # policy brings it back either way, but the log is what a human reads at 3am.
    def explode(_settings: Settings) -> None:
        raise OSError("connection refused")

    assert main([], run=explode) == EXIT_STARTUP_ERROR

    captured = capsys.readouterr()
    assert "connection refused" in captured.err
    assert "Traceback" not in captured.err


def test_an_operator_interrupt_is_a_clean_exit(
    minimal_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    # Ctrl-C and docker stop are normal, not failures. A non-zero exit would have the
    # container log a failure on every planned restart.
    def interrupt(_settings: Settings) -> None:
        raise KeyboardInterrupt

    assert main([], run=interrupt) == EXIT_OK
    assert capsys.readouterr().err == ""


async def test_serving_migrates_before_it_answers_anybody(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A fresh container has an empty directory and no tables. A bot that polls against a
    # missing table answers every user with an internal error and looks alive while it
    # does it.
    settings = pointed_at(monkeypatch, tmp_path)
    polled: list[object] = []

    async def poll(_application: object) -> None:
        polled.append(True)

    await serve(settings, poll=poll)

    assert polled, "the bot never polled"
    assert "users" in await table_names(settings)


async def test_serving_hands_the_poller_a_container_it_can_serve_from(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # What gets polled is a real application with a real container in place, or the first
    # update to arrive would find nothing to talk to.
    settings = pointed_at(monkeypatch, tmp_path)
    handed_to: list[object] = []

    async def poll(application: object) -> None:
        handed_to.append(application)

    await serve(settings, poll=poll)

    assert isinstance(handed_to[0].bot_data[SERVICES_KEY], Services)  # type: ignore[attr-defined]


async def test_serving_closes_the_database_when_polling_fails(
    minimal_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Polling raises on a bad token or an unreachable server. Leaking the engine there
    # leaves SQLite's file handle open, and the restart that follows ends up fighting the
    # previous process for the same file.
    settings = pointed_at(monkeypatch, tmp_path)
    closed: list[bool] = []
    original = Database.dispose

    async def record(self: Database) -> None:
        closed.append(True)
        await original(self)

    monkeypatch.setattr(Database, "dispose", record)

    async def refuse(_application: object) -> None:
        raise RuntimeError("cannot reach the Bot API server")

    with pytest.raises(RuntimeError, match="Bot API"):
        await serve(settings, poll=refuse)

    assert closed == [True]


async def test_serving_closes_the_database_after_a_clean_stop(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The ordinary path: docker stop sends a signal, polling returns, and the handle goes
    # with it.
    settings = pointed_at(monkeypatch, tmp_path)
    closed: list[bool] = []
    original = Database.dispose

    async def record(self: Database) -> None:
        closed.append(True)
        await original(self)

    monkeypatch.setattr(Database, "dispose", record)

    async def poll(_application: object) -> None:
        return None

    await serve(settings, poll=poll)

    assert closed == [True]


class RecordingJanitor:
    """A janitor that records what it was asked to do and then idles."""

    def __init__(self) -> None:
        self.swept = 0
        self.runs = 0
        self.stopped = 0

    async def sweep(self) -> SweepResult:
        self.swept += 1
        return SweepResult(removed=0, freed_bytes=0)

    async def run(self) -> None:
        self.runs += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped += 1


async def test_serving_sweeps_the_download_directory_before_it_answers_anybody(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A restart after a hard kill leaves files behind, and the first thing this process does
    # with that directory is clean it. Sweeping after polling starts would let the first
    # download be measured against space the sweep was about to give back.
    settings = pointed_at(monkeypatch, tmp_path)
    janitor = RecordingJanitor()
    events: list[str] = []

    async def poll(_application: object) -> None:
        events.append("polled")

    original_sweep = janitor.sweep

    async def record_sweep() -> SweepResult:
        events.append("swept")
        return await original_sweep()

    janitor.sweep = record_sweep  # type: ignore[method-assign]

    await serve(settings, poll=poll, janitor=janitor)

    assert events == ["swept", "polled"]


async def test_serving_keeps_sweeping_while_the_bot_runs(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # One pass is not a janitor. Without a task behind it, files that appear later are only
    # collected by the next restart.
    settings = pointed_at(monkeypatch, tmp_path)
    janitor = RecordingJanitor()

    async def poll(_application: object) -> None:
        # One yield, because a task that has not been given the loop yet has not started.
        # A bot serves for hours, so the periodic sweep is running long before this returns.
        await asyncio.sleep(0)
        assert janitor.runs == 1, "the periodic sweep was not running while the bot served"

    await serve(settings, poll=poll, janitor=janitor)


async def test_serving_stops_the_janitor_when_polling_fails(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Polling raises on a bad token or an unreachable server, and the process is on its way
    # out. A janitor task left running past that is a task the event loop will complain about
    # on its way down, holding the download directory open.
    settings = pointed_at(monkeypatch, tmp_path)
    janitor = RecordingJanitor()

    async def refuse(_application: object) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("cannot reach the Bot API server")

    with pytest.raises(RuntimeError, match="Bot API"):
        await serve(settings, poll=refuse, janitor=janitor)

    assert janitor.stopped == 1


async def test_serving_stops_the_janitor_after_a_clean_stop(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = pointed_at(monkeypatch, tmp_path)
    janitor = RecordingJanitor()

    async def poll(_application: object) -> None:
        await asyncio.sleep(0)

    await serve(settings, poll=poll, janitor=janitor)

    assert janitor.stopped == 1


async def test_the_janitor_is_built_from_the_configured_settings(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A janitor pointed at the wrong directory is worse than none: it would report a clean
    # bill of health while the real directory fills up.
    pointed_at(monkeypatch, tmp_path)
    monkeypatch.setenv("ORPHAN_TTL_SECONDS", "120")
    monkeypatch.setenv("JANITOR_INTERVAL_SECONDS", "300")
    configured = Settings()
    built: list[Janitor] = []
    original = main_module.build_janitor

    def record(given: Settings) -> Janitor:
        janitor = original(given)
        built.append(janitor)
        return janitor

    monkeypatch.setattr(main_module, "build_janitor", record)

    async def poll(_application: object) -> None:
        return None

    await serve(configured, poll=poll)

    assert built, "no janitor was built"
    assert built[0].directory == configured.download_dir
    assert built[0].ttl_seconds == 120
    assert built[0].interval_seconds == 300


async def test_a_startup_sweep_that_explodes_does_not_stop_the_bot_from_serving(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A janitor problem is a housekeeping problem. Refusing to start over one would turn a
    # full disk into a bot that cannot answer anybody, which is worse than the full disk.
    settings = pointed_at(monkeypatch, tmp_path)

    class Refusing(RecordingJanitor):
        async def sweep(self) -> SweepResult:
            raise PermissionError("the download directory is not writable")

    polled: list[object] = []

    async def poll(_application: object) -> None:
        polled.append(True)

    await serve(settings, poll=poll, janitor=Refusing())

    assert polled, "the bot never polled"


class FakeUpdater:
    """Stands in for the updater, which is where the polling actually happens.

    ``start_polling`` returns straight away, the way the real one does: it fetches in the
    background and hands back a queue. A fake that blocked here would make the tests below
    pass for the wrong reason, since the code under test idles after this call rather than
    inside it.
    """

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.stops = 0
        self.polling = asyncio.Event()

    async def start_polling(self, **kwargs: Any) -> None:
        self.started.append(kwargs)
        self.polling.set()

    async def stop(self) -> None:
        self.stops += 1


class FakeApplication:
    """The application as ``_poll`` uses it, and nothing more."""

    def __init__(self, updater: FakeUpdater) -> None:
        self.updater = updater
        self.initialised = 0
        self.shutdowns = 0

    async def initialize(self) -> None:
        self.initialised += 1

    async def shutdown(self) -> None:
        self.shutdowns += 1


async def poll_once(updater: FakeUpdater) -> None:
    """Start ``_poll``, wait until it is genuinely polling, then stop it."""
    task = asyncio.create_task(_poll(cast("BotApplication", FakeApplication(updater))))
    try:
        await asyncio.wait_for(updater.polling.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_polling_asks_for_exactly_the_updates_the_bot_answers() -> None:
    # Every update type left unset is fetched and then dropped by a bot with no handler for
    # it. Every type named here that the bot does not answer wastes a long-poll round trip.
    updater = FakeUpdater()

    await poll_once(updater)

    assert updater.started == [
        {"allowed_updates": ["message", "callback_query"], "drop_pending_updates": False}
    ]


async def test_polling_is_stopped_and_shut_down_when_the_process_is_stopped() -> None:
    # Ctrl-C and ``docker stop`` both land here. Shutting down is what closes the Bot API
    # session; skipping it on the way out leaves the server holding a connection it will
    # eventually time out, and the log ends with no explanation of why it stopped.
    updater = FakeUpdater()
    application = FakeApplication(updater)

    task = asyncio.create_task(_poll(cast("BotApplication", application)))
    await asyncio.wait_for(updater.polling.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert application.initialised == 1
    assert updater.stops == 1
    assert application.shutdowns == 1


async def test_polling_shuts_down_even_when_starting_it_failed() -> None:
    # A refused token or an unreachable server fails here. The connection has already been
    # opened by then, and the failure is the most likely moment to leave it dangling.
    class Refusing(FakeUpdater):
        async def start_polling(self, **kwargs: Any) -> None:
            raise OSError("connection refused")

    updater = Refusing()
    application = FakeApplication(updater)

    with pytest.raises(OSError, match="connection refused"):
        await _poll(cast("BotApplication", application))

    assert application.shutdowns == 1


def test_run_bot_serves_the_settings_it_was_given(
    minimal_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The last link in the chain: what the container runs, and what ``main`` hands its
    # validated settings to, have to be the same thing.
    settings = pointed_at(monkeypatch, tmp_path)
    served: list[Settings] = []

    async def record(given: Settings) -> None:
        served.append(given)

    monkeypatch.setattr(main_module, "serve", record)

    run_bot(settings)

    assert served == [settings]


def pointed_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """Settings whose database lives in this test's directory."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    return Settings()


async def table_names(settings: Settings) -> list[str]:
    """What SQLite actually has, read through a real connection."""
    database = Database(settings.database_url)
    try:
        async with database.engine.connect() as connection:
            return await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    finally:
        await database.dispose()
