from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import Settings
from app.ai import BidGenerator
from app.storage import StateStore

from .handlers import callbacks, commands


def build_bot(token: str) -> Bot:
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=False),
    )


def build_dispatcher(
    settings: Settings,
    store: StateStore,
    bid_generator: BidGenerator | None,
) -> Dispatcher:
    dp = Dispatcher()
    dp["settings"] = settings
    dp["store"] = store
    dp["bid_generator"] = bid_generator
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    return dp
