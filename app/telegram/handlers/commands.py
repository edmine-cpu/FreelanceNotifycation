import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings
from app.storage import StateStore

from ..views import settings_view, start_view

router = Router(name="commands")
log = logging.getLogger(__name__)


@router.message(CommandStart())
async def handle_start(
    message: Message, settings: Settings, store: StateStore, state: FSMContext,
) -> None:
    await state.clear()
    await store.set_active(True)
    text, markup = start_view(settings)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    log.info("sent /start menu to chat %s", message.chat.id)


@router.message(Command("help"))
async def handle_help(message: Message, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    text, markup = start_view(settings)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("settings"))
@router.message(Command("cancel"))
async def handle_settings(
    message: Message, settings: Settings, store: StateStore, state: FSMContext,
) -> None:
    await state.clear()
    text, markup = settings_view(settings, await store.muted_skill_ids(), await store.profile())
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("stop"))
async def handle_stop(message: Message, store: StateStore, state: FSMContext) -> None:
    await state.clear()
    await store.set_active(False)
    await message.answer("Твои уведомления остановлены. Настройки сохранены. /start — возобновить.")
