"""The one module that talks to yt-dlp.

yt-dlp is a blocking, thread-based library, so every call is pushed onto a worker
thread and progress arrives back through the event loop. Two consequences shape this
file: cancellation is checked inside yt-dlp's progress hook, because that is the only
place that runs often enough to notice, and the output template is fixed to the
configured download directory, because yt-dlp would otherwise write to the working
directory.

Throttling deliberately does not happen here. How often a chat message may be edited
is a Telegram concern, not a download concern, so that decision belongs to the layer
that owns the message.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from bot.domain.errors import (
    DownloadCancelledError,
    DownloadFailedError,
    VideoUnavailableError,
)
from bot.domain.models import DownloadProgress, VideoDescription, VideoMetadata
from bot.logging_setup import Logger, get_logger
from bot.services.youtube.formats import build_quality_menu

if TYPE_CHECKING:
    import concurrent.futures

    from bot.config import Settings
    from bot.domain.protocols import ProgressCallback

# The parameter is Any rather than dict[str, Any] because yt-dlp types its options as
# a TypedDict of the keys it happens to know about; anything forwarded to it is
# validated at runtime.
YdlFactory = Callable[[Any], Any]


def _default_factory(params: Any) -> YoutubeDL:
    """Wraps the class so the seam takes a callable rather than a type."""
    return YoutubeDL(params)


_UNAVAILABLE_MARKERS: Final = (
    "video unavailable",
    "private video",
    "members-only",
    "video has been removed",
    "account associated with this video has been terminated",
    "is not available in your country",
)

LOGGER_NAME: Final = "bot.services.youtube"


class YtDlpService:
    """A ``YoutubeService`` backed by yt-dlp."""

    def __init__(
        self,
        settings: Settings,
        *,
        ydl_factory: YdlFactory | None = None,
    ) -> None:
        self._settings = settings
        self._factory: YdlFactory = ydl_factory or _default_factory
        self._log: Logger = get_logger(LOGGER_NAME)

    # -- YoutubeService ---------------------------------------------------- #

    async def describe_video(self, url: str) -> VideoDescription:
        options = self._base_options()
        # Single-video, for the same reason as download_video: the accepted URLs can
        # still carry a list parameter.
        options["noplaylist"] = True
        info = await asyncio.to_thread(self._extract, url, download=False, options=options)

        metadata = VideoMetadata(
            video_id=str(info.get("id") or ""),
            title=str(info.get("title") or "Unknown title"),
            duration_seconds=_as_int(info.get("duration")),
            thumbnail_url=info.get("thumbnail"),
        )
        menu = build_quality_menu(
            _as_format_list(info.get("formats")),
            limit_bytes=self._settings.max_upload_bytes,
            allow_unknown_sizes=self._settings.allow_unknown_sizes,
        )
        return VideoDescription(metadata=metadata, menu=menu)

    async def download_video(
        self,
        url: str,
        format_id: str,
        *,
        on_progress: ProgressCallback | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> Path:
        loop = asyncio.get_running_loop()
        pending: list[concurrent.futures.Future[None]] = []

        def hook(raw: dict[str, Any]) -> None:
            if cancelled is not None and cancelled.is_set():
                # Raising from inside the hook is what actually stops yt-dlp; setting
                # a flag would not be noticed until the whole transfer had finished.
                raise DownloadCancelledError("the download was cancelled")
            if on_progress is None:
                return
            progress = _to_progress(raw)
            pending.append(asyncio.run_coroutine_threadsafe(on_progress(progress), loop))

        if cancelled is not None and cancelled.is_set():
            # Checked before starting too: the hook only runs once yt-dlp is already
            # transferring, which would make a cancel during setup look successful.
            raise DownloadCancelledError("the download was cancelled before it started")

        options = self._base_options()
        # Explicitly single-video. parse_link deliberately accepts a watch URL that
        # still carries a list parameter, and without this yt-dlp would return the
        # whole playlist, so the quality menu and the output filename would both
        # describe the wrong thing.
        options["noplaylist"] = True
        # No "+bestaudio": only progressive formats reach this method, and they
        # already carry sound. Merging an audio-only stream on top would drag ffmpeg
        # in and can change the container, which would make the filename we return a
        # lie and the upload fail.
        options["format"] = format_id
        options["progress_hooks"] = [hook]

        try:
            info = await asyncio.to_thread(self._extract, url, download=True, options=options)
        finally:
            # Drain the last progress callbacks before returning, so the final
            # "complete" update cannot arrive after the caller has moved on. The
            # wrapping happens here, on the loop's own thread, because that is where
            # a running loop exists to attach to.
            if pending:
                await asyncio.gather(
                    *(asyncio.wrap_future(future) for future in pending),
                    return_exceptions=True,
                )

        path = Path(str(info.get("_filename", "")))
        size_bytes = info.get("_size_bytes")
        if size_bytes is None:
            # yt-dlp can finish "successfully" having written nothing, and handing a
            # path that does not exist to Telegram produces an opaque API error. The
            # check happened on the worker thread, which is also why the file size is
            # already known here.
            raise DownloadFailedError(f"yt-dlp reported no file at {path}")

        self._log.info(
            "youtube.download.complete",
            url=url,
            format_id=format_id,
            path=str(path),
            size_bytes=size_bytes,
        )
        return path

    # -- Blocking calls, always on a worker thread ------------------------- #

    def _extract(
        self,
        url: str,
        *,
        download: bool,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._factory(options or self._base_options()) as ydl:
            try:
                info = ydl.extract_info(url, download=download)
            except (ExtractorError, DownloadError) as exc:
                raise _translate_ytdlp_error(exc) from exc
            if not download:
                return info or {}
            filename = ydl.prepare_filename(info or {})
            # The size is read here, on the worker thread, so no filesystem call
            # happens on the event loop.
            return {
                **(info or {}),
                "_filename": filename,
                "_size_bytes": _file_size(Path(filename)),
            }

    # -- Options ----------------------------------------------------------- #

    def _base_options(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _StructlogLogger(self._log),
            "outtmpl": str(self._settings.download_dir / "%(id)s.%(ext)s"),
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "cookies": self._cookies_file(),
        }

    def _cookies_file(self) -> str | None:
        cookies = self._settings.youtube_cookies_file
        return str(cookies) if cookies is not None and Path(cookies).exists() else None


class _StructlogLogger:
    """Routes yt-dlp's chatter into our structured log.

    Without this, yt-dlp writes its own lines to stderr in its own format, which is
    the one thing that makes a JSON log unusable.
    """

    def __init__(self, log: Logger) -> None:
        self._log = log

    def debug(self, message: str) -> None:
        self._log.debug("youtube.ydl.debug", detail=message)

    def info(self, message: str) -> None:
        self._log.debug("youtube.ydl.info", detail=message)

    def warning(self, message: str) -> None:
        self._log.warning("youtube.ydl.warning", detail=message)

    def error(self, message: str) -> None:
        self._log.error("youtube.ydl.error", detail=message)


def _translate_ytdlp_error(exc: Exception) -> Exception:
    """Turn yt-dlp's failures into something the user can act on.

    Availability problems arrive as either an ``ExtractorError`` or a
    ``DownloadError`` depending on which code path noticed, so the message is what
    decides, not the exception class.
    """
    detail = str(exc).lower()
    if any(marker in detail for marker in _UNAVAILABLE_MARKERS):
        return VideoUnavailableError(
            "YouTube will not serve that video. It may be private, members-only, or removed."
        )
    return DownloadFailedError(str(exc))


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _file_size(path: Path) -> int | None:
    """The size of a downloaded file, or None if it is not there.

    Reading it here, on the worker thread, keeps the filesystem off the event loop and
    doubles as proof that the file exists.
    """
    try:
        return path.stat().st_size
    except OSError:
        return None


def _as_format_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _to_progress(raw: dict[str, Any]) -> DownloadProgress:
    return DownloadProgress(
        downloaded_bytes=_as_int(raw.get("downloaded_bytes")) or 0,
        total_bytes=_as_int(raw.get("total_bytes")) or _as_int(raw.get("total_bytes_estimate")),
        speed_bytes_per_second=raw.get("speed"),
        eta_seconds=_as_int(raw.get("eta")),
    )
