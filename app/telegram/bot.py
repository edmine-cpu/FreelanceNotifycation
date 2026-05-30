from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import Settings
from app.ai import BidGenerator
from app.source import FreelancehuntSource
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
    source: FreelancehuntSource,
    prompt_examples_path: Path,
) -> Dispatcher:
    dp = Dispatcher()
    dp["settings"] = settings
    dp["store"] = store
    dp["bid_generator"] = bid_generator
    dp["source"] = source
    dp["prompt_examples_path"] = prompt_examples_path
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    return dp
