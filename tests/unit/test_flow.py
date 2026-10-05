"""Specification for per-chat flow state.

The behaviour worth pinning down is cancellation, because the two ways of cancelling a
transfer look identical from Telegram and are nothing alike in the code: setting the
flag lets the transfer unwind cleanly, while cancelling the task would leave cleanup
running inside a cancelled frame.
"""

from __future__ import annotations

from bot.domain.models import QualityMenu, QualityOption, VideoDescription, VideoMetadata
from bot.services.flow import Flow, PendingVideo

CHAT = 7
URL = "https://youtu.be/abc"


def a_pending(chat_id: int = CHAT) -> PendingVideo:
    option = QualityOption(format_id="22", height=720, size_bytes=1_000)
    description = VideoDescription(
        metadata=VideoMetadata(video_id="abc", title="A"),
        menu=QualityMenu(options=(option,)),
    )
    return PendingVideo(chat_id=chat_id, url=URL, description=description)


def test_a_chat_awaits_nothing_until_it_asks() -> None:
    assert Flow().expecting_link(CHAT) is False


def test_asking_for_a_video_makes_the_bot_wait_for_a_link() -> None:
    flow = Flow()

    flow.expect_link(CHAT)

    assert flow.expecting_link(CHAT) is True


def test_one_chat_waiting_does_not_implicate_another() -> None:
    flow = Flow()
    flow.expect_link(CHAT)

    assert flow.expecting_link(CHAT + 1) is False


def test_waiting_can_stop_without_remembering_how() -> None:
    flow = Flow()
    flow.expect_link(CHAT)

    flow.stop_expecting_link(CHAT)
    flow.stop_expecting_link(CHAT)

    assert flow.expecting_link(CHAT) is False


def test_a_shown_video_can_be_recalled_with_the_link_that_produced_it() -> None:
    # The download needs a URL, not just a format id, and it has to be the one the
    # description was built from.
    flow = Flow()
    pending = a_pending()

    flow.remember(pending)

    assert flow.recall(pending.chat_id) == pending


def test_forgetting_a_video_forgets_its_cancel_flag_too() -> None:
    # Leaving the flag behind would let a later download be cancelled by an old button.
    flow = Flow()
    flow.remember(a_pending())
    flow.begin(CHAT)

    flow.forget(CHAT)

    assert flow.recall(CHAT) is None
    assert flow.cancel(CHAT) is False


def test_a_new_description_replaces_the_previous_one() -> None:
    # A user who starts again should see the new video's qualities, not the old one's.
    flow = Flow()
    flow.remember(a_pending())
    flow.remember(a_pending())

    assert flow.recall(CHAT) is not None


def test_cancelling_with_nothing_running_says_so() -> None:
    assert Flow().cancel(CHAT) is False


def test_cancelling_a_running_download_sets_its_flag() -> None:
    flow = Flow()
    cancel = flow.begin(CHAT)

    assert flow.cancel(CHAT) is True
    assert cancel.is_set()


def test_cancelling_twice_is_harmless() -> None:
    # The Cancel button stays tappable until the message is replaced.
    flow = Flow()
    flow.begin(CHAT)

    assert flow.cancel(CHAT) is True
    assert flow.cancel(CHAT) is False


def test_a_finished_download_can_no_longer_be_cancelled() -> None:
    flow = Flow()
    flow.begin(CHAT)
    flow.end(CHAT)

    assert flow.cancel(CHAT) is False
    assert flow.is_running(CHAT) is False


def test_the_flag_comes_back_unset_for_each_download() -> None:
    # A second download reusing a set flag would look cancelled before it began.
    flow = Flow()

    first = flow.begin(CHAT)
    flow.cancel(CHAT)
    flow.end(CHAT)

    assert flow.begin(CHAT).is_set() is False
    assert first.is_set() is True
