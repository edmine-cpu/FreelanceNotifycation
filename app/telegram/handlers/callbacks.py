import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.config import Settings
from app.gemini import BidGenerator, BidGenerationError
from app.storage import StateStore

from .. import keyboards
from ..views import projects_page_view, start_view

router = Router(name="callbacks")
log = logging.getLogger(__name__)


@router.callback_query(F.data == keyboards.CALLBACK_NOOP)
async def handle_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_START)
async def handle_start_menu(callback: CallbackQuery, settings: Settings) -> None:
    text, markup = start_view(settings)
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_LIST_PREFIX))
async def handle_list_page(callback: CallbackQuery, settings: Settings, store: StateStore) -> None:
    page = _parse_page(callback.data or "")
    projects = await store.recent_projects()
    text, markup = projects_page_view(projects, page, settings)
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_REGEN_PREFIX))
async def handle_regen(
    callback: CallbackQuery,
    store: StateStore,
    bid_generator: BidGenerator | None,
) -> None:
    if bid_generator is None:
        await callback.answer("Gemini выключен в настройках", show_alert=True)
        return

    project_id = (callback.data or "")[len(keyboards.CALLBACK_REGEN_PREFIX):]
    project = await store.find_project(project_id)
    if project is None:
        await callback.answer("Проект не найден в истории", show_alert=True)
        return

    try:
        bid_text = await bid_generator.generate(project)
    except BidGenerationError:
        log.exception("regen failed for project %s", project_id)
        await callback.answer("Не удалось перегенерировать", show_alert=True)
        return

    try:
        await callback.message.edit_text(
            bid_text,
            reply_markup=keyboards.regen_bid_keyboard(project_id),
            parse_mode=None,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            log.exception("editMessageText failed for regen")
    await callback.answer("Перегенерировано")


async def _safe_edit(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        log.exception("editMessageText failed")


def _parse_page(data: str) -> int:
    raw = data[len(keyboards.CALLBACK_LIST_PREFIX):]
    try:
        return max(0, int(raw))
    except ValueError:
        return 0
