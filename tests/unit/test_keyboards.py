"""Specification for the keyboards the user actually sees.

The main menu's labels are a contract decision, so they are pinned here as literal text
rather than reconstructed from constants: if someone changes a label, this test is the
thing that stops them.
"""

from __future__ import annotations

from typing import Any

from bot.domain.models import (
    QualityMenu,
    QualityOption,
    RemovalReason,
    VideoDescription,
    VideoMetadata,
)
from bot.keyboards.inline import (
    CANCEL_CALLBACK,
    cancel_keyboard,
    quality_keyboard,
    quality_menu_text,
)
from bot.keyboards.reply import main_menu_keyboard

MB = 1_000_000


def rows_of(markup: Any) -> list[list[Any]]:
    """The buttons of a markup, as rows.

    Reply and inline keyboards expose their rows under different attribute names, and
    iterating the markup itself yields serialized dicts, so both are normalized here.
    """
    inner = getattr(markup, "inline_keyboard", None)
    if inner is None:
        inner = markup.keyboard
    return [list(row) for row in inner]


def labels(markup: Any) -> list[str]:
    return [button.text for row in rows_of(markup) for button in row]


def option(format_id: str, height: int, size_mb: float) -> QualityOption:
    return QualityOption(format_id=format_id, height=height, size_bytes=int(size_mb * MB))


def test_an_admin_sees_the_four_buttons_the_contract_names() -> None:
    assert labels(main_menu_keyboard(is_admin=True)) == [
        "🎬 Single video",
        "📀 Playlist",
        "❓ Help",
        "⚙️ Admin",
    ]


def test_a_normal_user_does_not_see_the_admin_button() -> None:
    # Leaking it would promise an admin screen that then refuses every action.
    assert labels(main_menu_keyboard(is_admin=False)) == [
        "🎬 Single video",
        "📀 Playlist",
        "❓ Help",
    ]


def test_an_admin_menu_is_a_square_grid() -> None:
    assert [len(row) for row in rows_of(main_menu_keyboard(is_admin=True))] == [2, 2]


def test_help_survives_for_everybody() -> None:
    # The admin button joins the help row rather than replacing anything, so a plain
    # user still gets the same help entry point.
    for is_admin in (True, False):
        assert "❓ Help" in labels(main_menu_keyboard(is_admin=is_admin))


def test_the_shared_row_does_not_reflow() -> None:
    # Single video and playlist sit together for everyone; a user who is later promoted
    # should not see the top row shift under their thumb.
    assert (
        labels(main_menu_keyboard(is_admin=True))[0:2]
        == labels(main_menu_keyboard(is_admin=False))[0:2]
    )


def description(*options: QualityOption) -> VideoDescription:
    return VideoDescription(
        metadata=VideoMetadata(video_id="dQw4w9WgXcQ", title="A Song", duration_seconds=225),
        menu=QualityMenu(options=options, limit_bytes=2000 * MB),
    )


def test_each_option_becomes_a_button_labelled_with_quality_and_size() -> None:
    assert labels(quality_keyboard(description(option("22", 720, 48.2)))) == ["720p — 48.2 MB"]


def test_a_button_carries_its_format_id_so_the_handler_need_not_guess() -> None:
    keyboard = quality_keyboard(description(option("22", 720, 48.2)))

    assert rows_of(keyboard)[0][0].callback_data == "quality:22"


def test_the_menu_states_the_title_and_duration() -> None:
    text = quality_menu_text(description(option("22", 720, 48.2)))

    assert "A Song" in text
    assert "3:45" in text


def test_the_menu_explains_what_was_removed() -> None:
    menu = QualityMenu(
        options=(option("18", 360, 20),),
        removed=(QualityMenu.Removed(option("22", 720, 2400), reason=RemovalReason.TOO_LARGE),),
        limit_bytes=2000 * MB,
    )

    assert "Removed 1 over the 2.0 GB upload limit" in quality_menu_text(
        VideoDescription(metadata=VideoMetadata(video_id="x", title="A"), menu=menu),
    )


def test_nothing_is_removed_so_nothing_is_said() -> None:
    text = quality_menu_text(description(option("22", 720, 48.2)))

    assert "Removed" not in text


def test_the_cancel_button_says_what_it_does() -> None:
    keyboard = cancel_keyboard()

    assert rows_of(keyboard)[0][0].text == "✖ Cancel"
    assert rows_of(keyboard)[0][0].callback_data == CANCEL_CALLBACK
