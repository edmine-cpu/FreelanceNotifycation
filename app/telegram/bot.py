from collections.abc import Callable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation

from app.config import Settings
from app.ai import BidGenerator
from app.source import FreelancehuntSource
from app.storage.users import UserContext, UserRegistry

from .handlers import callbacks, commands
from .user_context import UserContextMiddleware


def build_bot(token: str) -> Bot:
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=False),
    )


def build_dispatcher(
    settings: Settings,
    registry: UserRegistry,
    generator_factory: Callable[[UserContext], BidGenerator | None],
    source: FreelancehuntSource,
) -> Dispatcher:
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    dp["source"] = source
    middleware = UserContextMiddleware(registry, generator_factory)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    return dp
