from collections.abc import Callable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation

from app.config import Settings
from app.ai import BidGenerator
from app.source import FreelancehuntSource
from app.storage.users import UserContext, UserRegistry

from .handlers import callbacks, commands, ai_actions
from app.ai.store import AIStore
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
    *, ai_store: AIStore | None = None,
) -> Dispatcher:
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    dp["source"] = source
    dp["ai_store"] = ai_store or AIStore(settings.state_file.parent / "ai.sqlite3")
    middleware = UserContextMiddleware(registry, generator_factory, dp["ai_store"])
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(ai_actions.router)
    return dp
