"""The services a handler is allowed to reach for.

Kept in its own module so handlers and the application can both import it without
importing each other. A handler that reached for a global, or for the application
builder, would be untestable in isolation; a container that the handlers import reads
the same way from either side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from telegram.ext import Application, CallbackContext, ExtBot, JobQueue

    from bot.config import Settings
    from bot.domain.protocols import DiskRoom, YoutubeService
    from bot.services.files import FileDelivery
    from bot.services.flow import Flow
    from bot.services.users import UserService

    #: The context type every handler here is written against.
    #:
    #: The three dictionaries are spelled out rather than left as ``object`` because
    #: python-telegram-bot binds them. The bot keeps exactly one thing in bot_data and
    #: nothing in the other two, so plain dictionaries are the truth here rather than a
    #: convenience.
    BotData = dict[str, object]
    BotContext = CallbackContext[ExtBot[object], BotData, BotData, BotData]

    #: The application the handlers are registered against.
    #:
    #: The job queue is ``None`` because nothing here schedules work: downloads are
    #: driven by updates, not by a clock.
    BotApplication = Application[
        ExtBot[object],
        BotContext,
        BotData,
        BotData,
        BotData,
        JobQueue[BotContext] | None,
    ]

#: Where the container is kept in ``bot_data``.
SERVICES_KEY = "services"


@dataclass(frozen=True, slots=True)
class Services:
    """Everything a handler needs, assembled once at startup."""

    settings: Settings
    users: UserService
    videos: YoutubeService
    delivery: FileDelivery
    flow: Flow
    #: Last, and checked before anything is written: the disk has to have room for a
    #: download before a download is started.
    disk: DiskRoom


def services_from(context: BotContext) -> Services:
    """The container the application was built with.

    ``bot_data`` is an untyped dictionary by design, so this is the one place where the
    bot trusts its own earlier work rather than checking it.
    """
    return cast("Services", context.application.bot_data[SERVICES_KEY])
