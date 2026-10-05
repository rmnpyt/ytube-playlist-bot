"""Building Telegram updates, and catching what the bot tries to send back.

Handlers are tested through a real ``Application.process_update`` rather than by calling
them directly. Routing is the part most likely to be wrong — a filter that matches too
much, or a handler registered in the wrong order, is invisible to a test that skips the
dispatcher — and going through the dispatcher is also what makes these tests cover the
registration functions at all.

Nothing here reaches the network. ``Bot._post`` is the single point every outbound call
funnels through, so replacing it records the calls and answers with objects the bot
already knows how to hand back.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from telegram import (
    Bot,
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
    Update,
    User,
)
from telegram.constants import MessageEntityType
from telegram.error import BadRequest, TelegramError

if TYPE_CHECKING:
    import pytest
    from telegram import TelegramObject

DEFAULT_CHAT_ID = 1001
DEFAULT_USER_ID = 2002
DEFAULT_BOT_ID = 42

#: Endpoints whose reply is a ``Message`` rather than a bare boolean.
_MESSAGE_ENDPOINTS = frozenset(
    {
        "sendMessage",
        "editMessageText",
        "sendVideo",
        "sendDocument",
        "sendPhoto",
    }
)

#: Every field an ``Update`` can carry something in that needs a bot of its own.
_UPDATE_PARTS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
)


def wire(update: Update, bot: Bot) -> Update:
    """Attach a bot to an update and everything inside it.

    Real updates come out of ``Update.de_json`` already carrying the bot that fetched
    them, which is what lets a handler call ``message.reply_text`` or ``query.answer``
    without passing a bot around. Neither ``Update.set_bot`` nor ``CallbackQuery.set_bot``
    recurses, so a hand-built update needs every part of itself wired up by hand.
    """
    for part in _UPDATE_PARTS:
        _attach(getattr(update, part, None), bot)
    update.set_bot(bot)
    return update


def _attach(obj: TelegramObject | None, bot: Bot) -> None:
    """Set the bot on an object and on the message it is hanging off, if any."""
    while obj is not None:
        obj.set_bot(bot)
        obj = getattr(obj, "message", None)


@dataclass(frozen=True, slots=True)
class Call:
    """One outbound Telegram API call."""

    endpoint: str
    data: dict[str, Any]

    @property
    def text(self) -> str | None:
        """The text this call would have shown a user."""
        value = self.data.get("text")
        return None if value is None else str(value)

    @property
    def buttons(self) -> list[str]:
        """Every button label this call would have shown, reply or inline.

        The keyboard arrives as a live object, not as text. ``Bot._post`` is where PTB
        serialises it into the JSON Telegram receives, and this stub stands in for that
        method, so the conversion happens downstream of here. Reading only one shape
        would quietly report "no buttons" for whichever one was missed.
        """
        markup = self.data.get("reply_markup")
        if isinstance(markup, str):
            try:
                markup = json.loads(markup)
            except ValueError:
                return []
        if isinstance(markup, InlineKeyboardMarkup):
            rows: Any = markup.inline_keyboard
        elif isinstance(markup, ReplyKeyboardMarkup):
            rows = markup.keyboard
        elif isinstance(markup, dict):
            rows = markup.get("keyboard") or markup.get("inline_keyboard") or []
        else:
            return []
        labels: list[str] = []
        for row in rows:
            labels.extend(str(button.text) for button in row)
        return labels


@dataclass(slots=True)
class RecordingTelegram:
    """Stands in for the Telegram API."""

    calls: list[Call] = field(default_factory=list)
    sent: int = 0
    refused: dict[str, TelegramError] = field(default_factory=dict)

    def refuse(self, endpoint: str, error: TelegramError | None = None) -> None:
        """Make one endpoint fail, the way Telegram fails some requests.

        Needed where the bot's own recovery is the thing under test: a callback query that
        has been answerable for too long, a sendMessage to somebody who blocked the bot. A
        stub that always succeeds cannot show whether those cases are handled or ignored.
        """
        self.refused[endpoint] = error or BadRequest("Bad Request: simulated refusal")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Take over every outbound call for the duration of a test."""
        monkeypatch.setattr(Bot, "_post", self._post)

    async def _post(self, endpoint: str, data: Any = None, **kwargs: Any) -> Any:
        # ``data`` is positional-optional on Bot._post; handlers always send it.
        self.calls.append(Call(endpoint=endpoint, data=dict(data or {})))
        if endpoint in self.refused:
            raise self.refused[endpoint]
        if endpoint == "getMe":
            # Initialising the application asks the API who the bot is. Real tests
            # still want to dispatch updates, so the question has to have an answer.
            # A dict, because that is the shape a real response body carries, and
            # ExtBot re-reads it into a User.
            return a_bot_user().to_dict()
        if endpoint not in _MESSAGE_ENDPOINTS:
            return True
        self.sent += 1
        return _message_body(
            chat_id=_chat_id_of(data),
            text=str((data or {}).get("text") or ""),
            message_id=self.sent,
        )

    # -- Reading what happened --------------------------------------------- #

    @property
    def endpoints(self) -> list[str]:
        return [call.endpoint for call in self.calls]

    def texts(self, endpoint: str) -> list[str]:
        texts = (call.text for call in self.calls if call.endpoint == endpoint)
        return [text for text in texts if text is not None]

    @property
    def said(self) -> list[str]:
        """Every text the bot put in front of the user, in order."""
        return [call.text for call in self.calls if call.text is not None]

    @property
    def buttons_shown(self) -> list[str]:
        """Every button label the bot offered, in order."""
        return [label for call in self.calls for label in call.buttons]

    def edits(self) -> list[Call]:
        return [call for call in self.calls if call.endpoint == "editMessageText"]


def _chat_id_of(data: Any) -> int:
    if isinstance(data, dict):
        return int(data.get("chat_id") or DEFAULT_CHAT_ID)
    return DEFAULT_CHAT_ID


def a_chat(chat_id: int = DEFAULT_CHAT_ID) -> Chat:
    return Chat(id=chat_id, type=Chat.PRIVATE)


def a_user(user_id: int = DEFAULT_USER_ID, username: str | None = "tester") -> User:
    return User(id=user_id, first_name="Test", is_bot=False, username=username)


def a_bot_user() -> User:
    """The bot's own identity, which ``Application.initialize`` goes looking for."""
    return User(
        id=DEFAULT_BOT_ID,
        first_name="Test",
        is_bot=True,
        username="test_bot",
    )


def _message_body(chat_id: int, text: str, message_id: int) -> dict[str, Any]:
    """The JSON body a real ``sendMessage`` would come back with.

    ``Bot._post`` returns whatever the API sent, and the typed methods turn it into
    objects with ``Message.de_json``. Handing back a ready-made ``Message`` looks
    helpful and is not: PTB hands it straight to ``de_json``, which expects keys, not
    objects. ``date`` in particular has to be a timestamp, because that is what the wire
    format carries and what ``de_json`` converts.

    Every message gets its own id, which is what lets a button press find the menu it
    came from.
    """
    return {
        "message_id": message_id,
        "date": int(dt.datetime.now(tz=dt.UTC).timestamp()),
        "chat": {"id": chat_id, "type": Chat.PRIVATE},
        "from": a_bot_user().to_dict(),
        "text": text,
    }


def a_message(
    *,
    chat_id: int = DEFAULT_CHAT_ID,
    user_id: int = DEFAULT_USER_ID,
    text: str | None = None,
    message_id: int = 1,
    command: bool = False,
) -> Message:
    return Message(
        message_id=message_id,
        date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        chat=a_chat(chat_id),
        from_user=a_user(user_id),
        text=text,
        entities=_command_entity(text) if command else None,
    )


def _command_entity(text: str | None) -> list[MessageEntity] | None:
    """The entity Telegram attaches to a command.

    ``filters.COMMAND`` reads the entities, never the text: a message that merely starts
    with a slash is not a command to Telegram, and a handler that relied on the text
    instead would accept messages the real API would never route to it.
    """
    if text is None:
        return None
    return [MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=0, length=len(text))]


def a_text_update(
    text: str,
    *,
    chat_id: int = DEFAULT_CHAT_ID,
    user_id: int = DEFAULT_USER_ID,
) -> Update:
    """A message that arrived as ordinary text."""
    return Update(
        update_id=1,
        message=a_message(chat_id=chat_id, user_id=user_id, text=text),
    )


def a_command_update(
    command: str,
    *,
    chat_id: int = DEFAULT_CHAT_ID,
    user_id: int = DEFAULT_USER_ID,
) -> Update:
    """A message whose text is a command, addressed as ``/command``."""
    message = a_message(chat_id=chat_id, user_id=user_id, text=f"/{command}", command=True)
    return Update(update_id=1, message=message)


def a_callback_update(
    data: str,
    *,
    chat_id: int = DEFAULT_CHAT_ID,
    user_id: int = DEFAULT_USER_ID,
    message_text: str = "Pick a quality",
) -> Update:
    """A tap on an inline button."""
    message = a_message(chat_id=chat_id, user_id=user_id, text=message_text, message_id=2)
    return Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="callback-1",
            from_user=a_user(user_id),
            chat_instance="instance",
            data=data,
            message=message,
        ),
    )
