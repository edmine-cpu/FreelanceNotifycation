import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery

from app.config import Settings
from app.ai import BidGenerator, BidGenerationError
from app.llm import QuotaExceededError
from app.source import FreelancehuntSource
from app.storage import StateStore

from .. import formatting, keyboards
from ..views import projects_page_view, start_view

router = Router(name="callbacks")
log = logging.getLogger(__name__)

_QUOTA_NOTICE = "Лимит запросов к ИИ исчерпан. Попробуйте позже."


@router.callback_query(F.data == keyboards.CALLBACK_NOOP)
async def handle_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_HIDE)
async def handle_hide(callback: CallbackQuery) -> None:
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        log.exception("delete message failed")
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_START)
async def handle_start_menu(callback: CallbackQuery, settings: Settings) -> None:
    text, markup = start_view(settings)
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_LIST_PREFIX))
async def handle_list_page(callback: CallbackQuery, settings: Settings, store: StateStore) -> None:
    filter_key, page = _parse_list_data(callback.data or "")
    projects = await store.recent_projects()
    passed = await store.passed_ids()
    text, markup = projects_page_view(projects, page, settings, filter_key, passed_ids=passed)
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_RAW_PREFIX))
async def handle_raw_page(
    callback: CallbackQuery,
    settings: Settings,
    store: StateStore,
    source: FreelancehuntSource,
) -> None:
    skill_id, page = _parse_raw_data(callback.data or "")
    # Live, unfiltered view: fetch the category fresh on entry (page 0) and merge
    # new projects into the cache so Show/Generate can find them later. On a
    # fetch error, fall back to the cached history rather than failing the tap.
    try:
        fetched = await source.fetch_category(skill_id)
        await store.add_projects(fetched)
        projects = fetched
    except Exception:
        log.exception("raw fetch failed for skill %s", skill_id)
        projects = [p for p in await store.recent_projects() if p.skill_id == skill_id]
    text, markup = projects_page_view(projects, page, settings, str(skill_id), unfiltered=True)
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_SHOW_PREFIX))
async def handle_show_project(
    callback: CallbackQuery,
    store: StateStore,
) -> None:
    project_id = (callback.data or "")[len(keyboards.CALLBACK_SHOW_PREFIX):]
    project = await store.find_project(project_id)
    if project is None:
        await callback.answer("Проект не найден в истории", show_alert=True)
        return

    text = formatting.format_project_notification(project)
    try:
        await callback.message.answer(
            text,
            reply_markup=keyboards.project_detail_keyboard(project),
        )
    except TelegramAPIError:
        log.exception("failed to send project detail for %s", project_id)
        await callback.answer("Не удалось отправить", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_GEN_PREFIX))
async def handle_generate(
    callback: CallbackQuery,
    store: StateStore,
    bid_generator: BidGenerator | None,
) -> None:
    if bid_generator is None:
        await callback.answer("ИИ выключен в настройках", show_alert=True)
        return

    project_id = (callback.data or "")[len(keyboards.CALLBACK_GEN_PREFIX):]
    project = await store.find_project(project_id)
    if project is None:
        await callback.answer("Проект не найден в истории", show_alert=True)
        return

    await callback.answer("Генерирую…")
    try:
        bid_text = await bid_generator.generate(project)
    except QuotaExceededError:
        log.warning("ai quota exhausted on generate for project %s", project_id)
        await _send_notice(callback, _QUOTA_NOTICE)
        return
    except BidGenerationError:
        log.exception("generate failed for project %s", project_id)
        await _send_notice(callback, "Не удалось сгенерировать ответ")
        return

    try:
        await callback.message.reply(
            bid_text,
            reply_markup=keyboards.regen_bid_keyboard(project_id),
            parse_mode=None,
        )
    except TelegramAPIError:
        log.exception("failed to send generated bid for %s", project_id)


@router.callback_query(F.data.startswith(keyboards.CALLBACK_REGEN_PREFIX))
async def handle_regen(
    callback: CallbackQuery,
    store: StateStore,
    bid_generator: BidGenerator | None,
) -> None:
    if bid_generator is None:
        await callback.answer("ИИ выключен в настройках", show_alert=True)
        return

    project_id = (callback.data or "")[len(keyboards.CALLBACK_REGEN_PREFIX):]
    project = await store.find_project(project_id)
    if project is None:
        await callback.answer("Проект не найден в истории", show_alert=True)
        return

    await callback.answer("Перегенерирую…")
    try:
        bid_text = await bid_generator.generate(project)
    except QuotaExceededError:
        log.warning("ai quota exhausted on regen for project %s", project_id)
        await _send_notice(callback, _QUOTA_NOTICE)
        return
    except BidGenerationError:
        log.exception("regen failed for project %s", project_id)
        await _send_notice(callback, "Не удалось перегенерировать ответ")
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


async def _send_notice(callback: CallbackQuery, text: str) -> None:
    """Send a plain follow-up message; the callback query is already answered."""
    try:
        await callback.message.answer(text)
    except TelegramAPIError:
        log.exception("failed to send notice: %s", text)


async def _safe_edit(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        log.exception("editMessageText failed")


def _parse_list_data(data: str) -> tuple[str, int]:
    """Parse a `list:<filter>:<page>` callback into (filter_key, page)."""
    body = data[len(keyboards.CALLBACK_LIST_PREFIX):]
    parts = body.split(":")
    filter_key = parts[0] if parts and parts[0] else keyboards.LIST_FILTER_ALL
    page = 0
    if len(parts) > 1:
        try:
            page = max(0, int(parts[1]))
        except ValueError:
            page = 0
    return filter_key, page


def _parse_raw_data(data: str) -> tuple[int, int]:
    """Parse a `raw:<skill_id>:<page>` callback into (skill_id, page)."""
    body = data[len(keyboards.CALLBACK_RAW_PREFIX):]
    parts = body.split(":")
    try:
        skill_id = int(parts[0])
    except (ValueError, IndexError):
        skill_id = 0
    page = 0
    if len(parts) > 1:
        try:
            page = max(0, int(parts[1]))
        except ValueError:
            page = 0
    return skill_id, page
