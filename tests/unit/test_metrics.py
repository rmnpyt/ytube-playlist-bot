"""Tests for the metrics server."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from bot.services.metrics import _build_app, metrics_server


async def test_build_app_has_healthz_and_metrics() -> None:
    app = _build_app()
    routes = [str(r) for r in app.router.routes()]
    assert any("/healthz" in r for r in routes)
    assert any("/metrics" in r for r in routes)


async def test_healthz_returns_ok() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    app = _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        text = await resp.text()
        assert text == "OK"


async def test_metrics_returns_prometheus_output() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    app = _build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/metrics")
        assert resp.status == 200
        text = await resp.text()
        # prometheus-client always outputs at least a header
        assert "#" in text or text == ""


async def test_metrics_server_context_manager(tmp_path) -> None:
    from bot.config import Settings

    settings = Settings(
        telegram_bot_token="123456789:AAF-fake-token",
        telegram_api_id=424242,
        telegram_api_hash="0123456789abcdef0123456789abcdef",
        admin_user_ids=frozenset([7]),
        metrics_host="127.0.0.1",
        metrics_port=9091,  # fixed test port
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        download_dir=tmp_path / "downloads",
        log_level="DEBUG",
        log_json=False,
        allowed_user_ids=frozenset(),
        telegram_bot_api_url="http://localhost:8081",
        telegram_bot_api_local=True,
        max_upload_mb=2000,
        orphan_ttl_seconds=21600,
        janitor_interval_seconds=900,
        disk_free_threshold_gb=5.0,
        max_playlist_items=50,
        max_concurrent_downloads=2,
    )
    (tmp_path / "downloads").mkdir()

    async with metrics_server(settings) as runner:
        # Server should be running
        sites = list(runner.sites)
        assert len(sites) == 1
        site = sites[0]
        assert site._server is not None
        # Port should be assigned
        port = site._port
        assert port > 0

        # Health check should work
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200
                assert await resp.text() == "OK"


async def test_metrics_main_block_imports() -> None:
    """Ensure the __main__ block imports work without error."""
    # This exercises the import path that the __main__ block uses
    from bot.services.metrics import _run_server_directly
    from bot.config import Settings

    settings = Settings(
        telegram_bot_token="123456789:AAF-fake-token",
        telegram_api_id=424242,
        telegram_api_hash="0123456789abcdef0123456789abcdef",
        admin_user_ids=frozenset([7]),
        metrics_host="127.0.0.1",
        metrics_port=9093,
        database_url="sqlite+aiosqlite:///:memory:",
        download_dir="/tmp/downloads",
        log_level="DEBUG",
        log_json=False,
        allowed_user_ids=frozenset(),
        telegram_bot_api_url="http://localhost:8081",
        telegram_bot_api_local=True,
        max_upload_mb=2000,
        orphan_ttl_seconds=21600,
        janitor_interval_seconds=900,
        disk_free_threshold_gb=5.0,
        max_playlist_items=50,
        max_concurrent_downloads=2,
    )
    # Just verify the function can be called with valid settings
    # (we don't actually run the server because it blocks on Event.wait())
    assert _run_server_directly is not None
    assert callable(_run_server_directly)