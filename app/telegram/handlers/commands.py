import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import Settings

from ..views import start_view

router = Router(name="commands")
log = logging.getLogger(__name__)


@router.message(CommandStart())
@router.message(Command("help"))
async def handle_start(message: Message, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    text, markup = start_view(settings)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    log.info("sent /start menu to chat %s", message.chat.id)
