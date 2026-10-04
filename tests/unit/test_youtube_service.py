"""Specification for the yt-dlp adapter.

Handlers depend on the ``YoutubeService`` protocol, never on yt-dlp. These tests
drive the real adapter against a stand-in for yt-dlp's own object, so what is being
verified is our translation: yt-dlp's dicts into domain values, its exceptions into
domain errors, and its progress hook into a throttled async callback.

The fake mirrors yt-dlp's public shape. That is the contract we are coding against.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from structlog.testing import capture_logs
from yt_dlp.utils import DownloadError

from bot.config import Settings
from bot.domain.errors import (
    AllOptionsTooLargeError,
    DownloadCancelledError,
    DownloadFailedError,
    VideoUnavailableError,
)
from bot.domain.models import VideoDescription
from bot.domain.protocols import YoutubeService
from bot.logging_setup import configure_logging, get_logger
from bot.services.youtube.service import YtDlpService, _StructlogLogger

MB = 1_000_000
VIDEO_ID = "dQw4w9WgXcQ"


def test_the_adapter_satisfies_the_protocol_handlers_will_depend_on(
    settings: Settings, tmp_path: Path
) -> None:
    # Cheap insurance against a rename that would only surface when a handler is
    # written. Signatures are checked by mypy; this checks the shape.
    assert isinstance(make_service(settings, tmp_path), YoutubeService)


def test_ytdlp_chatter_is_routed_into_our_structured_log() -> None:
    # yt-dlp writes to stderr in its own format unless given a logger, which is the one
    # thing that makes a JSON log unusable. Its "info" is very chatty, so it is
    # deliberately demoted to debug rather than flooding the log at INFO.
    configure_logging(level="DEBUG", json_output=True, secret_values=[])
    adapter = _StructlogLogger(get_logger("bot.services.youtube"))

    with capture_logs() as captured:
        adapter.debug("a detail")
        adapter.info("noisy chatter")
        adapter.warning("careful")
        adapter.error("broken")

    emitted = {entry["event"]: entry["log_level"] for entry in captured}
    assert emitted == {
        "youtube.ydl.debug": "debug",
        "youtube.ydl.info": "debug",  # demoted on purpose
        "youtube.ydl.warning": "warning",
        "youtube.ydl.error": "error",
    }
    assert any(entry.get("detail") == "noisy chatter" for entry in captured)


def progressive(format_id: str, height: int, size: int) -> dict[str, Any]:
    return {
        "format_id": format_id,
        "height": height,
        "vcodec": "avc1",
        "acodec": "mp4a",
        "ext": "mp4",
        "filesize": size,
    }


def info(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "dQw4w9WgXcQ",
        "title": "A Song",
        "duration": 225,
        "thumbnail": "https://example.invalid/t.jpg",
        "formats": [progressive("18", 360, 20 * MB), progressive("22", 720, 48 * MB)],
    }
    return {**payload, **overrides}


class FakeYDL:
    """Stands in for ``yt_dlp.YoutubeDL``."""

    instances: ClassVar[list[FakeYDL]] = []

    def __init__(
        self,
        params: dict[str, Any],
        *,
        extract_result: dict[str, Any] | None = None,
        extract_error: Exception | None = None,
        download_error: Exception | None = None,
        target: str = "/data/downloads/video.mp4",
        no_file: bool = False,
    ) -> None:
        self.params = params
        self._extract_result = extract_result
        self._extract_error = extract_error
        self._download_error = download_error
        self._target = target
        self._no_file = no_file
        self.extract_calls: list[tuple[str, bool]] = []
        self.download_calls: list[list[str]] = []
        FakeYDL.instances.append(self)

    def __enter__(self) -> FakeYDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        self.extract_calls.append((url, download))
        if download and self._download_error is not None:
            # A real yt-dlp failure surfaces from extract_info when download=True.
            raise self._download_error
        if self._extract_error is not None:
            raise self._extract_error
        assert self._extract_result is not None
        if download:
            for percent in (50, 100):
                self._fire_hook(
                    percent=percent,
                    downloaded_bytes=48 * MB * percent // 100,
                    total_bytes=48 * MB,
                    speed=3 * MB,
                    eta=1 if percent == 50 else 0,
                )
                # Real transfers take time, which lets the event loop run the
                # progress callbacks this thread just scheduled.
                time.sleep(0.01)
            if not self._no_file:
                Path(self._target).write_bytes(b"video")
        return self._extract_result

    def download(self, urls: list[str]) -> None:
        self.download_calls.append(urls)

    def prepare_filename(self, payload: dict[str, Any]) -> str:
        return self._target

    def _fire_hook(self, **progress: Any) -> None:
        for hook in self.params.get("progress_hooks", []):
            hook(progress)


@pytest.fixture(autouse=True)
def _clear_instances() -> None:
    FakeYDL.instances.clear()


def make_service(
    settings: Settings,
    tmp_path: Path,
    *,
    extract_result: dict[str, Any] | None = None,
    extract_error: Exception | None = None,
    download_error: Exception | None = None,
    target: str | None = None,
    no_file: bool = False,
) -> YtDlpService:
    def factory(params: dict[str, Any]) -> FakeYDL:
        return FakeYDL(
            params,
            extract_result=extract_result if extract_result is not None else info(),
            extract_error=extract_error,
            download_error=download_error,
            target=target or str(tmp_path / "video.mp4"),
            no_file=no_file,
        )

    return YtDlpService(settings, ydl_factory=factory)


async def test_describing_a_video_returns_metadata_and_a_menu(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(settings, tmp_path)

    described = await service.describe_video("https://youtu.be/dQw4w9WgXcQ")

    assert isinstance(described, VideoDescription)
    assert described.metadata.title == "A Song"
    assert described.metadata.duration_seconds == 225
    assert [option.height for option in described.menu.options] == [720, 360]


async def test_describing_a_video_does_not_download_anything(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(settings, tmp_path)

    await service.describe_video("https://youtu.be/dQw4w9WgXcQ")

    assert FakeYDL.instances[0].extract_calls == [("https://youtu.be/dQw4w9WgXcQ", False)]
    assert not FakeYDL.instances[0].download_calls


async def test_the_upload_ceiling_from_settings_is_applied(
    settings: Settings, tmp_path: Path
) -> None:
    oversized = info(formats=[progressive("18", 360, 2_400 * MB)])
    service = make_service(settings, tmp_path, extract_result=oversized)

    # The 2000 MB default comes from settings, so this also proves the adapter reads
    # the ceiling rather than hard-coding it.
    with pytest.raises(AllOptionsTooLargeError) as caught:
        await service.describe_video("https://youtu.be/dQw4w9WgXcQ")

    assert caught.value.limit_bytes == 2_000 * MB


async def test_a_private_or_missing_video_is_reported_as_unavailable(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(
        settings,
        tmp_path,
        extract_error=DownloadError("Video unavailable"),
    )

    with pytest.raises(VideoUnavailableError):
        await service.describe_video("https://youtu.be/dQw4w9WgXcQ")


async def test_any_other_yt_dlp_failure_becomes_a_download_failure(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(
        settings,
        tmp_path,
        extract_error=DownloadError("HTTP Error 429: Too Many Requests"),
    )

    with pytest.raises(DownloadFailedError):
        await service.describe_video("https://youtu.be/dQw4w9WgXcQ")


async def test_downloading_returns_the_file_path(settings: Settings, tmp_path: Path) -> None:
    service = make_service(settings, tmp_path)

    path = await service.download_video(
        "https://youtu.be/dQw4w9WgXcQ", "22", cancelled=asyncio.Event()
    )

    assert path == tmp_path / "video.mp4"
    assert path.is_file()


async def test_progress_is_translated_into_domain_values(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(settings, tmp_path)
    seen: list[Any] = []

    async def on_progress(progress: Any) -> None:
        seen.append(progress)

    await service.download_video(
        "https://youtu.be/dQw4w9WgXcQ",
        "22",
        on_progress=on_progress,
        cancelled=asyncio.Event(),
    )

    assert seen
    assert seen[-1].percent == 100
    assert seen[-1].total_bytes == 48 * MB


async def test_no_progress_callback_is_fine(settings: Settings, tmp_path: Path) -> None:
    service = make_service(settings, tmp_path)

    path = await service.download_video(
        "https://youtu.be/dQw4w9WgXcQ", "22", cancelled=asyncio.Event()
    )

    assert path.is_file()


async def test_yt_dlp_asking_for_the_progressive_format_alone(
    settings: Settings, tmp_path: Path
) -> None:
    # Progressive formats already contain audio. Merging bestaudio on top would pull
    # ffmpeg in and can change the container out from under the filename we return.
    service = make_service(settings, tmp_path)

    await service.download_video("https://youtu.be/dQw4w9WgXcQ", "22", cancelled=asyncio.Event())

    assert FakeYDL.instances[0].params["format"] == "22"


@pytest.mark.parametrize("operation", ["describe", "download"])
async def test_yt_dlp_is_never_allowed_to_expand_a_playlist(
    settings: Settings, tmp_path: Path, operation: str
) -> None:
    # parse_link accepts a watch URL that still carries ?list=, so yt-dlp must be told
    # not to follow it. Otherwise it returns the whole playlist and both the quality
    # menu and the output filename describe the wrong thing.
    service = make_service(settings, tmp_path)

    if operation == "describe":
        await service.describe_video(f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PLabc")
    else:
        await service.download_video(
            f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PLabc",
            "22",
            cancelled=asyncio.Event(),
        )

    assert FakeYDL.instances[0].params["noplaylist"] is True


async def test_a_cancel_set_before_the_download_stops_it_before_any_transfer(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(settings, tmp_path)
    cancelled = asyncio.Event()
    cancelled.set()

    with pytest.raises(DownloadCancelledError):
        await service.download_video("https://youtu.be/dQw4w9WgXcQ", "22", cancelled=cancelled)

    # Nothing was even started, so no hooks fired and no file was named.
    assert FakeYDL.instances == []


async def test_a_cancel_while_the_transfer_runs_stops_the_download(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(settings, tmp_path)
    cancelled = asyncio.Event()

    async def on_progress(_progress: Any) -> None:
        cancelled.set()

    with pytest.raises(DownloadCancelledError):
        await service.download_video(
            "https://youtu.be/dQw4w9WgXcQ",
            "22",
            on_progress=on_progress,
            cancelled=cancelled,
        )

    # The transfer started, then was abandoned mid-flight.
    assert FakeYDL.instances[0].extract_calls == [("https://youtu.be/dQw4w9WgXcQ", True)]


async def test_a_download_failure_is_wrapped(settings: Settings, tmp_path: Path) -> None:
    service = make_service(
        settings,
        tmp_path,
        download_error=DownloadError("fragment 3 not found"),
    )

    with pytest.raises(DownloadFailedError):
        await service.download_video(
            "https://youtu.be/dQw4w9WgXcQ", "22", cancelled=asyncio.Event()
        )


async def test_a_download_that_produced_no_file_is_a_failure(
    settings: Settings, tmp_path: Path
) -> None:
    # yt-dlp sometimes "succeeds" without writing anything. Returning a path that
    # does not exist would push an opaque Telegram API error at the user instead.
    service = make_service(settings, tmp_path, extract_result=info(), no_file=True)

    with pytest.raises(DownloadFailedError):
        await service.download_video(
            "https://youtu.be/dQw4w9WgXcQ", "22", cancelled=asyncio.Event()
        )


async def test_yt_dlp_is_told_to_be_quiet(settings: Settings, tmp_path: Path) -> None:
    # yt-dlp prints to stdout by default, which corrupts container logs.
    service = make_service(settings, tmp_path)

    await service.download_video("https://youtu.be/dQw4w9WgXcQ", "22", cancelled=asyncio.Event())

    params = FakeYDL.instances[0].params
    assert params["quiet"] is True
    assert params["noprogress"] is True
    assert params["logger"] is not None


async def test_the_event_loop_keeps_running_while_a_download_is_in_flight(
    settings: Settings, tmp_path: Path
) -> None:
    service = make_service(settings, tmp_path)
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    beat = asyncio.create_task(heartbeat())
    try:
        await service.download_video("https://youtu.be/dQw4w9WgXcQ", "22")
    finally:
        beat.cancel()

    # yt-dlp is blocking and thread-based; calling it inline would starve the loop and
    # freeze every other conversation in the process.
    assert ticks > 0


async def test_downloads_land_inside_the_configured_directory(
    minimal_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    service = make_service(Settings(), tmp_path)

    await service.download_video("https://youtu.be/dQw4w9WgXcQ", "22", cancelled=asyncio.Event())

    assert FakeYDL.instances[0].params["outtmpl"].startswith(str(tmp_path))
