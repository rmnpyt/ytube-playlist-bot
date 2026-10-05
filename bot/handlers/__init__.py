"""Telegram command, message and callback handlers.

Each module here exposes ``register``, so the application wires the bot together in one
place instead of every handler module reaching for the application itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from telegram import Update

    from bot.container import BotContext

    #: A handler as python-telegram-bot calls one.
    #:
    #: The return type is a coroutine rather than a bare awaitable because that is what
    #: the library's handler classes declare, and a handler typed as anything broader
    #: cannot be handed to them.
    Handler = Callable[[Update, BotContext], Coroutine[Any, Any, None]]
