"""Specification for delivering a file and cleaning up after it."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot.domain.errors import DownloadCancelledError
from bot.services.files import MAX_CAPTION_LENGTH, FileDelivery, fit_caption


class RecordingBot:
    def __init__(self) -> None:
        self.videos: list[tuple[int, Path, str | None]] = []
        self.documents: list[tuple[int, Path, str | None]] = []

    async def send_video(self, chat_id: int, video: Path, caption: str | None = None) -> None:
        self.videos.append((chat_id, video, caption))

    async def send_document(
        self,
        chat_id: int,
        document: Path,
        caption: str | None = None,
    ) -> None:
        self.documents.append((chat_id, document, caption))


def a_file(tmp_path: Path, name: str = "A Song.mp4", contents: str = "data") -> Path:
    path = tmp_path / name
    path.write_text(contents)
    return path


async def test_a_video_preference_is_honoured(tmp_path: Path) -> None:
    bot, path = RecordingBot(), a_file(tmp_path)

    await FileDelivery(bot).deliver(42, path, as_video=True)

    assert bot.videos == [(42, path, "A Song")]
    assert bot.documents == []


async def test_a_document_preference_is_honoured(tmp_path: Path) -> None:
    # A user who has been burned by a codec the phone cannot play asked for the file
    # rather than the video, and that has to reach Telegram's document path.
    bot, path = RecordingBot(), a_file(tmp_path)

    await FileDelivery(bot).deliver(42, path, as_video=False)

    assert bot.documents == [(42, path, "A Song")]
    assert bot.videos == []


@pytest.mark.parametrize(
    ("length", "expected_length"),
    [
        (10, 10),
        (MAX_CAPTION_LENGTH, MAX_CAPTION_LENGTH),
        (MAX_CAPTION_LENGTH + 1, MAX_CAPTION_LENGTH),
    ],
)
def test_a_caption_is_fitted_to_what_telegram_accepts(length: int, expected_length: int) -> None:
    assert len(fit_caption("x" * length)) == expected_length


def test_an_over_long_caption_ends_in_an_ellipsis() -> None:
    # A caption that stops mid-word reads as corruption; one that visibly stops reads
    # as truncated, which is what happened.
    fitted = fit_caption("x" * (MAX_CAPTION_LENGTH + 50))

    assert fitted.endswith("…")


async def test_the_file_is_removed_after_delivery(tmp_path: Path) -> None:
    bot, path = RecordingBot(), a_file(tmp_path)
    delivery = FileDelivery(bot)

    try:
        await delivery.deliver(42, path, as_video=True)
    finally:
        await delivery.discard(path)

    assert not path.exists()


async def test_a_partial_file_is_removed_when_the_download_is_cancelled(tmp_path: Path) -> None:
    # The user pressed Cancel, so half a video is on disk and nobody will ever want it.
    bot, path = RecordingBot(), a_file(tmp_path, contents="half a file")
    delivery = FileDelivery(bot)

    try:
        raise DownloadCancelledError("cancelled")
    except DownloadCancelledError:
        await delivery.discard(path)

    assert not path.exists()


async def test_a_cancelled_task_still_leaves_no_file_behind(tmp_path: Path) -> None:
    # Cancelling a task raises inside it; the cleanup has to survive that, because this
    # is the path the Cancel button actually takes.
    path = a_file(tmp_path, contents="half a file")
    delivery = FileDelivery(RecordingBot())

    async def download_then_cancel() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            await delivery.discard(path)

    task = asyncio.ensure_future(download_then_cancel())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not path.exists()


async def test_cleaning_up_a_file_that_was_never_created_is_not_an_error(tmp_path: Path) -> None:
    # Cancellation can land before the first byte is written.
    assert await FileDelivery(RecordingBot()).discard(tmp_path / "never-existed.mp4") is True
