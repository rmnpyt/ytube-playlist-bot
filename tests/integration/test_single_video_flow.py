"""The single-video flow, from a tap on "Single video" to a file in the chat.

Every test here goes through a real ``Application`` so routing is exercised along with
behaviour. That matters most where the two interact: a menu label is text, and so is a
link, and only the registration order keeps them apart.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from tests.fakes import FakeDelivery, FakeDisk, FakeUsers, FakeVideos, a_description, an_option
from tests.telegram_stub import (
    DEFAULT_CHAT_ID,
    DEFAULT_USER_ID,
    RecordingTelegram,
    a_callback_update,
    a_command_update,
    a_text_update,
    wire,
)

from bot.container import SERVICES_KEY, Services
from bot.domain.errors import AllOptionsTooLargeError, InternalError, VideoUnavailableError
from bot.domain.models import QualityMenu, RemovalReason
from bot.keyboards.inline import CANCEL_CALLBACK, QUALITY_PREFIX
from bot.keyboards.reply import ADMIN_LABEL, HELP_LABEL, PLAYLIST_LABEL, SINGLE_VIDEO_LABEL
from bot.services.files import FileDelivery
from bot.services.flow import PendingVideo

if TYPE_CHECKING:
    from telegram import Update

    from bot.container import BotApplication

#: A link that describes cleanly.
URL = "https://youtu.be/abcdefghijk"
#: A link the adapter would refuse.
BAD_URL = "not a link at all"


def wired(services: Services, **changes: object) -> Services:
    """The same container with some fakes swapped out."""
    return replace(services, **changes)  # type: ignore[arg-type]


async def send(application: BotApplication, services: Services, update: Update) -> None:
    """Put the container in place and dispatch one update through the real dispatcher."""
    application.bot_data[SERVICES_KEY] = services
    # Real updates arrive with the bot already attached, so every reply shortcut works
    # without a bot being passed around. Ours are built by hand and PTB's dispatcher
    # will not do it for us, so every ``reply_text`` would raise instead of answering.
    wire(update, application.bot)
    await application.process_update(update)


def offer_video(services: Services) -> Services:
    """A container with a described video waiting to be chosen."""
    return wired(services, videos=FakeVideos(a_description()))


async def offered(application: BotApplication, services: Services, **changes: object) -> Services:
    """Drive the container the way a user does, and hand back what they were given.

    Tapping "Single video" first is not a formality. The bot only takes a link from a
    chat that asked for one, so a test that pastes a URL into a chat which never tapped
    anything is testing the fallback rather than the flow. Every test past this point
    wants a video described and a menu on screen, which is what this leaves behind.
    """
    flow = wired(services, **{"videos": FakeVideos(a_description()), **changes})
    await send(application, flow, a_text_update(SINGLE_VIDEO_LABEL))
    await send(application, flow, a_text_update(URL))
    return flow


# -- The menu --------------------------------------------------------------- #


async def test_start_greets_with_the_menu(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_command_update("start"))

    assert telegram.texts("sendMessage") == ["What would you like to do?"]
    assert telegram.buttons_shown == [SINGLE_VIDEO_LABEL, PLAYLIST_LABEL, HELP_LABEL]


async def test_the_admin_button_is_only_offered_to_admins(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_command_update("start", user_id=7))

    assert telegram.buttons_shown == [SINGLE_VIDEO_LABEL, PLAYLIST_LABEL, HELP_LABEL, ADMIN_LABEL]


async def test_the_admin_button_is_hidden_from_everybody_else(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_command_update("start"))

    assert ADMIN_LABEL not in telegram.buttons_shown


async def test_help_explains_the_bot(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_command_update("help"))

    assert "YouTube link" in telegram.said[-1]


async def test_the_help_button_and_the_help_command_agree(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_command_update("help"))
    from_command = telegram.said[-1]

    telegram.calls.clear()
    await send(application, services, a_text_update(HELP_LABEL))

    assert telegram.said[-1] == from_command


async def test_a_nobody_wants_the_admin_button(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The button is not shown, but the label is only text on the wire and anybody can
    # type it, so the check cannot live in the keyboard.
    await send(application, services, a_text_update(ADMIN_LABEL))

    assert telegram.said == ["This bot is by invitation. Ask one of its admins for access."]


async def test_the_playlist_button_starts_playlist_mode(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_text_update(PLAYLIST_LABEL))

    assert "Playlist mode" in telegram.said[-1]
    assert "Send YouTube links" in telegram.said[-1]


async def test_stray_text_is_handed_back_to_the_menu(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The catch-all is registered last, so this reached it rather than the single-video
    # handler. If that order ever changes, this test is the one that says so.
    await send(application, services, a_text_update("hello?"))

    assert telegram.said == ["Tap one of the buttons, or send a link."]


# -- Access ----------------------------------------------------------------- #


async def test_a_denied_user_is_told_so_and_gets_nothing_else(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    denied = wired(services, users=FakeUsers(allowed=False))

    await send(application, denied, a_command_update("start"))

    assert telegram.said == ["This bot is by invitation. Ask one of its admins for access."]
    assert not denied.flow.expecting_link(DEFAULT_CHAT_ID)


# -- Taking a link ---------------------------------------------------------- #


async def test_a_tap_asks_for_a_link(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_text_update(SINGLE_VIDEO_LABEL))

    assert telegram.said == ["Send me a YouTube link."]
    assert services.flow.expecting_link(DEFAULT_CHAT_ID)


async def test_a_link_is_described_and_offered_as_a_menu(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = wired(services, videos=FakeVideos(a_description()))
    await send(application, flow, a_text_update(SINGLE_VIDEO_LABEL))

    await send(application, flow, a_text_update(URL))

    offered = flow.flow.recall(DEFAULT_CHAT_ID)
    assert isinstance(offered, PendingVideo)
    assert offered.url == URL
    assert "A Video" in telegram.said[-1]
    assert not flow.flow.expecting_link(DEFAULT_CHAT_ID)


async def test_a_link_that_cannot_be_read_keeps_the_flow_open(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The user pasted rubbish. They should be able to try again without going back
    # through the menu, so the flow stays open and no menu is stored.
    flow = wired(services, videos=FakeVideos(error=VideoUnavailableError("that is not a video")))
    await send(application, flow, a_text_update(SINGLE_VIDEO_LABEL))

    await send(application, flow, a_text_update(BAD_URL))

    assert telegram.said[-1] == "that is not a video"
    assert flow.flow.expecting_link(DEFAULT_CHAT_ID)
    assert flow.flow.recall(DEFAULT_CHAT_ID) is None


async def test_every_quality_over_the_ceiling_is_explained_and_nothing_is_offered(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # A 4K video uploaded by nobody: every format is over the limit, so there is no
    # quality to press. The user gets the reason in the same units the limit is set in,
    # and no dead menu.
    videos = FakeVideos(
        error=AllOptionsTooLargeError(largest_bytes=2_400_000_000, limit_bytes=2_000_000_000),
    )
    flow = wired(services, videos=videos)
    await send(application, flow, a_text_update(SINGLE_VIDEO_LABEL))

    await send(application, flow, a_text_update(URL))

    assert telegram.said[-1] == (
        "the smallest available quality is 2.4 GB, still over the 2.0 GB upload limit"
    )
    assert telegram.buttons_shown == []
    assert flow.flow.recall(DEFAULT_CHAT_ID) is None
    # Still listening: the user can paste a different video without going back through
    # the menu, which is the difference between a graceful stop and a broken one.
    assert flow.flow.expecting_link(DEFAULT_CHAT_ID)
    assert videos.described == [URL]
    assert videos.downloads == []


async def test_a_link_typed_without_asking_is_handed_back_to_the_menu(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # A link in a chat that never tapped anything. Nothing is described and nothing is
    # stored: the bot only reads a link from a chat that asked for one, so the answer is
    # the same one anybody else would get for text they were not expecting.
    flow = offer_video(services)

    await send(application, flow, a_text_update(URL))

    assert flow.videos.described == []  # type: ignore[attr-defined]
    assert flow.flow.recall(DEFAULT_CHAT_ID) is None
    assert telegram.said[-1] == "Tap one of the buttons, or send a link."


async def test_a_second_link_needs_a_second_tap(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # Describing a video closes the request for a link, so the menu is spent. The buttons
    # on the old menu are dead too, which is why the user is pointed back at the keyboard.
    flow = await offered(application, services)

    await send(application, flow, a_text_update(URL))

    assert sum(1 for text in telegram.texts("sendMessage") if text.startswith("A Video")) == 1
    assert telegram.said[-1] == "Tap one of the buttons, or send a link."


async def test_text_after_a_menu_is_handed_back_to_the_menu(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The link was already described, so this second message is not a link being asked
    # for a second time. It falls through to the catch-all.
    flow = await offered(application, services)

    await send(application, flow, a_text_update("never mind"))

    assert telegram.said[-1] == "Tap one of the buttons, or send a link."


# -- Choosing a quality ----------------------------------------------------- #


async def test_a_chosen_quality_is_downloaded_and_sent(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = await offered(application, services, delivery=FileDelivery(application.bot))

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert flow.videos.downloads == [(URL, "137")]  # type: ignore[attr-defined]
    assert telegram.endpoints.count("sendVideo") == 1


async def test_the_file_is_removed_after_it_is_sent(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = await offered(application, services, delivery=FakeDelivery())

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert flow.delivery.discarded == ["A Video.mp4"]  # type: ignore[attr-defined]


async def test_a_full_disk_refuses_the_download_before_it_starts(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The whole point of the guard: the transfer is never attempted. Starting it anyway
    # and failing afterwards would leave a half-written file on the volume the guard was
    # asked to protect.
    flow = await offered(application, services, disk=FakeDisk(room=False))

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert flow.videos.downloads == []  # type: ignore[attr-defined]
    assert telegram.endpoints.count("sendVideo") == 0


async def test_a_full_disk_is_explained_rather_than_swallowed(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The user is told the disk is full and to try later, in words rather than as a traceback
    # or a bare "error": there is nothing they can fix, so the message has to say what to do.
    flow = await offered(application, services, disk=FakeDisk(room=False))

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    said = telegram.said[-1]
    assert "disk" in said.lower()
    assert "later" in said.lower()


async def test_a_refused_download_leaves_nothing_to_cancel(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # A transfer that never began must not leave a cancel button waiting on a download that
    # is not running, or the user's next press asks to stop nothing.
    flow = await offered(application, services, disk=FakeDisk(room=False))

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert not flow.flow.is_running(DEFAULT_CHAT_ID)


async def test_a_disk_with_room_downloads_normally(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The guard checks on every download, so it has to stay invisible when the answer is
    # yes. A test that only covered the refusal would pass with a guard that always refuses.
    flow = await offered(application, services, disk=FakeDisk(room=True))

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert flow.videos.downloads == [(URL, "137")]  # type: ignore[attr-defined]


async def test_a_document_preference_is_honoured_end_to_end(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = await offered(
        application,
        services,
        delivery=FileDelivery(application.bot),
        users=FakeUsers(send_as_video=False),
    )

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert telegram.endpoints.count("sendDocument") == 1
    assert telegram.endpoints.count("sendVideo") == 0


async def test_progress_reaches_the_user(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = await offered(application, services)
    telegram.calls.clear()

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert any("100%" in text for text in telegram.texts("editMessageText"))


async def test_pressing_the_same_button_twice_does_not_download_twice(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # One menu, one download. The buttons on it are not re-enabled until a new video has
    # been described, because Telegram keeps them there until the message goes away.
    flow = await offered(application, services)

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))
    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert len(flow.videos.downloads) == 1  # type: ignore[attr-defined]
    assert telegram.said[-1] == "That menu has expired. Send the link again."


async def test_a_forged_button_is_refused(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # A format nobody was offered. Treated like an expired menu rather than like a bug,
    # and no download is attempted.
    flow = await offered(application, services)

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}999"))

    assert flow.videos.downloads == []  # type: ignore[attr-defined]
    assert telegram.said[-1] == "That menu has expired. Send the link again."


async def test_a_button_pressed_before_any_link_is_refused(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = offer_video(services)

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert telegram.said[-1] == "That menu has expired. Send the link again."
    assert flow.videos.downloads == []  # type: ignore[attr-defined]


async def test_the_callback_is_answered_before_anything_slow_happens(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # Telegram shows a spinner until the query is answered. Answering after the transfer
    # would leave it spinning for the length of the download.
    flow = await offered(application, services)
    telegram.calls.clear()

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert telegram.endpoints[0] == "answerCallbackQuery"


async def test_the_pending_entry_remembers_the_url_not_just_the_menu(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = await offered(application, services)
    stored = flow.flow.recall(DEFAULT_CHAT_ID)
    assert isinstance(stored, PendingVideo)

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert flow.videos.downloads == [(stored.url, "137")]  # type: ignore[attr-defined]


async def test_a_removed_quality_is_explained_on_the_menu(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    huge = an_option("299", 2160, 90_000_000)
    description = a_description(
        removed=(QualityMenu.Removed(huge, RemovalReason.TOO_LARGE),),
        limit_bytes=50_000_000,
    )
    flow = wired(services, videos=FakeVideos(description))
    await send(application, flow, a_text_update(SINGLE_VIDEO_LABEL))

    await send(application, flow, a_text_update(URL))

    said = telegram.said[-1]
    assert "Removed 1 over the 50.0 MB upload limit" in said


# -- Failing downloads ------------------------------------------------------ #


async def test_a_failed_download_reports_why_and_sends_nothing(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    delivery = FakeDelivery()
    flow = await offered(
        application,
        services,
        videos=FakeVideos(a_description(), download_error=VideoUnavailableError("gone")),
        delivery=delivery,
    )

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert telegram.said[-1] == "gone"
    assert delivery.delivered == []


async def test_an_internal_failure_is_not_explained_to_the_user(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The detail belongs in the log. A traceback in the chat helps nobody, and tells them
    # more about the bot than they asked for.
    flow = await offered(
        application,
        services,
        videos=FakeVideos(
            a_description(), download_error=InternalError("connection reset by peer")
        ),
    )

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert telegram.said[-1] == "Something went wrong and the download stopped. Try again."
    assert "connection reset" not in " ".join(telegram.said)


async def test_a_file_that_downloaded_but_could_not_be_sent_is_explained(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    delivery = FakeDelivery(error=VideoUnavailableError("file too big"))
    flow = await offered(application, services, delivery=delivery)

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert telegram.said[-1] == "The file downloaded but could not be sent. Please try again."
    assert delivery.discarded == ["A Video.mp4"]


async def test_a_failed_download_does_not_stay_cancellable(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = await offered(
        application,
        services,
        videos=FakeVideos(a_description(), download_error=VideoUnavailableError("gone")),
    )

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert not flow.flow.is_running(DEFAULT_CHAT_ID)


# -- Cancelling ------------------------------------------------------------- #


async def test_cancelling_when_nothing_runs_says_so(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    await send(application, services, a_callback_update(CANCEL_CALLBACK))

    assert telegram.said[-1] == "There is no download to cancel."


async def test_a_cancelled_download_is_not_sent(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    delivery = FakeDelivery()
    videos = FakeVideos(a_description(), cancel_during=True)
    flow = await offered(application, services, videos=videos, delivery=delivery)

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert telegram.said[-1] == "Cancelled."
    assert videos.was_cancelled
    assert delivery.delivered == []


async def test_a_cancelled_download_leaves_nothing_to_send_but_a_clean_path(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    delivery = FakeDelivery()
    flow = await offered(
        application,
        services,
        videos=FakeVideos(a_description(), cancel_during=True),
        delivery=delivery,
    )

    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    assert delivery.discarded == []
    assert not flow.flow.is_running(DEFAULT_CHAT_ID)


async def test_cancelling_a_finished_download_has_nothing_to_do(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    flow = await offered(application, services)
    await send(application, flow, a_callback_update(f"{QUALITY_PREFIX}137"))

    await send(application, flow, a_callback_update(CANCEL_CALLBACK))

    assert telegram.said[-1] == "There is no download to cancel."


# -- Sightings -------------------------------------------------------------- #


async def test_every_update_is_recorded_before_it_is_answered(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The bot must have something to show an admin before anybody has asked for anything.
    users = FakeUsers()
    flow = wired(services, users=users)

    await send(application, flow, a_command_update("start"))

    assert [seen.telegram_id for seen in users.sightings] == [DEFAULT_USER_ID]
    assert [seen.username for seen in users.sightings] == ["tester"]


async def test_a_denied_user_is_recorded_too(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # Being refused is exactly when somebody needs to be visible in the admin list.
    users = FakeUsers(allowed=False)
    flow = wired(services, users=users)

    await send(application, flow, a_command_update("start"))

    assert [seen.telegram_id for seen in users.sightings] == [DEFAULT_USER_ID]


async def test_one_update_is_recorded_once_not_twice(
    application: BotApplication,
    telegram: RecordingTelegram,
    services: Services,
) -> None:
    # The single-video handler falls through to the menu's catch-all. That catch-all is
    # undecorated for exactly this reason, and this test is what keeps it that way.
    users = FakeUsers()
    flow = wired(services, users=users)

    await send(application, flow, a_text_update(URL))

    assert len(users.sightings) == 1
