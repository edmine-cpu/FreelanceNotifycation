import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.config import Settings, parse_skill_ids
from app.ai import BidGenerator, BidGenerationError
from app.ai.prompt_store import PromptJsonError, read_prompt_json, write_prompt_json
from app.llm import QuotaExceededError
from app.source import FreelancehuntSource
from app.storage import StateStore

from .. import formatting, keyboards
from ..views import (
    category_names_view,
    category_notifications_view,
    projects_page_view,
    settings_view,
    start_view,
)

router = Router(name="callbacks")
log = logging.getLogger(__name__)

_QUOTA_NOTICE = "Лимит запросов к ИИ исчерпан. Попробуйте позже."
_TELEGRAM_TEXT_LIMIT = 4096
_MAX_CATEGORY_NAME_LENGTH = 80


class SettingsFlow(StatesGroup):
    awaiting_category_id = State()
    awaiting_category_name = State()
    awaiting_prompt_json = State()


@router.callback_query(F.data == keyboards.CALLBACK_NOOP)
async def handle_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_HIDE)
async def handle_hide(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        log.exception("delete message failed")
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_START)
async def handle_start_menu(callback: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    text, markup = start_view(settings)
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_SETTINGS)
async def handle_settings_menu(
    callback: CallbackQuery,
    settings: Settings,
    store: StateStore,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    await state.clear()
    text, markup = settings_view(settings, await store.muted_skill_ids())
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_ADD_CATEGORY)
async def handle_add_category_menu(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    await state.set_state(SettingsFlow.awaiting_category_id)
    await _remember_menu_message(callback, state)
    await _safe_edit(callback, formatting.format_add_category_prompt(), keyboards.settings_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_PROMPT_JSON)
async def handle_prompt_json(
    callback: CallbackQuery,
    settings: Settings,
    prompt_examples_path: Path,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    await state.clear()
    try:
        text = read_prompt_json(prompt_examples_path)
    except PromptJsonError as exc:
        await _send_settings_flow_message(
            callback,
            formatting.format_settings_notice(str(exc)),
            keyboards.hide_keyboard(),
        )
        await callback.answer()
        return
    if len(text) > _TELEGRAM_TEXT_LIMIT:
        await callback.answer("JSON больше лимита одного сообщения Telegram", show_alert=True)
        return
    try:
        await callback.message.answer(
            text,
            reply_markup=keyboards.prompt_json_keyboard(),
            parse_mode=None,
        )
    except TelegramAPIError:
        log.exception("failed to send prompt JSON")
        await callback.answer("Не удалось отправить JSON", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_CATEGORY_NAMES)
async def handle_category_names_menu(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    await state.clear()
    text, markup = category_names_view(settings)
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_EDIT_CATEGORY_NAME_PREFIX))
async def handle_category_name_edit_menu(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    skill_id = _parse_skill_id_suffix(callback.data or "", keyboards.CALLBACK_EDIT_CATEGORY_NAME_PREFIX)
    category = _find_category(settings, skill_id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await state.set_state(SettingsFlow.awaiting_category_name)
    await state.update_data(category_name_skill_id=skill_id)
    await _remember_menu_message(callback, state)
    await _safe_edit(
        callback,
        formatting.format_category_name_prompt(category.skill_id, category.name),
        keyboards.category_names_back_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_PROMPT_EDIT)
async def handle_prompt_edit_menu(
    callback: CallbackQuery,
    settings: Settings,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    await state.set_state(SettingsFlow.awaiting_prompt_json)
    await _remember_menu_message(callback, state)
    await _safe_edit(callback, formatting.format_prompt_edit_prompt(), keyboards.hide_keyboard())
    await callback.answer()


@router.callback_query(F.data == keyboards.CALLBACK_NOTIFICATIONS)
async def handle_notifications_menu(
    callback: CallbackQuery,
    settings: Settings,
    store: StateStore,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    await state.clear()
    text, markup = category_notifications_view(settings, await store.muted_skill_ids())
    await _safe_edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_TOGGLE_MUTE_PREFIX))
async def handle_toggle_category_notifications(
    callback: CallbackQuery,
    settings: Settings,
    store: StateStore,
) -> None:
    if not await _ensure_callback_allowed(callback, settings):
        return
    skill_id = _parse_skill_id_suffix(callback.data or "", keyboards.CALLBACK_TOGGLE_MUTE_PREFIX)
    if skill_id <= 0:
        await callback.answer("Некорректная категория", show_alert=True)
        return
    muted = await store.toggle_muted_skill_id(skill_id)
    text, markup = category_notifications_view(settings, await store.muted_skill_ids())
    await _safe_edit(callback, text, markup)
    await callback.answer("Уведомления выключены" if muted else "Уведомления включены")


@router.message(SettingsFlow.awaiting_category_id)
async def handle_category_id_message(
    message: Message,
    settings: Settings,
    store: StateStore,
    source: FreelancehuntSource,
    state: FSMContext,
) -> None:
    if not _is_allowed_chat(message.chat.id, settings):
        await _delete_user_message(message)
        return
    raw = (message.text or "").strip()
    try:
        skill_id = int(raw)
    except ValueError:
        await _edit_menu_from_state(
            message,
            state,
            formatting.format_settings_notice("ID категории должен быть числом. Отправь ID ещё раз."),
            keyboards.settings_back_keyboard(),
        )
        await _delete_user_message(message)
        return
    if skill_id <= 0:
        await _edit_menu_from_state(
            message,
            state,
            formatting.format_settings_notice("ID категории должен быть положительным числом."),
            keyboards.settings_back_keyboard(),
        )
        await _delete_user_message(message)
        return

    fallback = parse_skill_ids(settings.skill_ids)
    added = await store.add_skill_id(skill_id, fallback)
    skill_ids = await store.skill_ids(fallback)
    settings.skill_ids = ",".join(str(item) for item in skill_ids)
    source.set_categories(settings.categories)
    await _delete_user_message(message)

    if added:
        text = formatting.format_settings_notice(
            f"Категория #{skill_id} добавлена. Имя можно задать в разделе «Имена категорий»."
        )
    else:
        text = formatting.format_settings_notice(f"Категория #{skill_id} уже есть в списке.")
    await _edit_menu_from_state(message, state, text, keyboards.settings_keyboard())
    await state.clear()


@router.message(SettingsFlow.awaiting_category_name)
async def handle_category_name_message(
    message: Message,
    settings: Settings,
    store: StateStore,
    source: FreelancehuntSource,
    state: FSMContext,
) -> None:
    if not _is_allowed_chat(message.chat.id, settings):
        await _delete_user_message(message)
        return

    data = await state.get_data()
    try:
        skill_id = int(data.get("category_name_skill_id", 0))
    except (TypeError, ValueError):
        skill_id = 0
    category = _find_category(settings, skill_id)
    if category is None:
        await _delete_user_message(message)
        await _edit_menu_from_state(
            message,
            state,
            formatting.format_settings_notice("Категория не найдена. Выбери её заново."),
            keyboards.category_names_keyboard(settings.categories),
        )
        await state.clear()
        return

    name = _normalize_category_name(message.text or "")
    if not name:
        await _edit_menu_from_state(
            message,
            state,
            formatting.format_settings_notice("Имя не должно быть пустым. Отправь имя ещё раз."),
            keyboards.category_names_back_keyboard(),
        )
        await _delete_user_message(message)
        return
    if len(name) > _MAX_CATEGORY_NAME_LENGTH:
        await _edit_menu_from_state(
            message,
            state,
            formatting.format_settings_notice(
                f"Имя слишком длинное: максимум {_MAX_CATEGORY_NAME_LENGTH} символов."
            ),
            keyboards.category_names_back_keyboard(),
        )
        await _delete_user_message(message)
        return

    await store.set_category_name(skill_id, name)
    settings.category_names = await store.category_names(settings.category_names)
    source.set_categories(settings.categories)
    await _delete_user_message(message)
    text, markup = category_names_view(settings)
    await _edit_menu_from_state(message, state, text, markup)
    await state.clear()


@router.message(SettingsFlow.awaiting_prompt_json)
async def handle_prompt_json_message(
    message: Message,
    settings: Settings,
    bid_generator: BidGenerator | None,
    prompt_examples_path: Path,
    state: FSMContext,
) -> None:
    if not _is_allowed_chat(message.chat.id, settings):
        await _delete_user_message(message)
        return
    raw = message.text or ""
    if not raw.strip():
        await _edit_menu_from_state(
            message,
            state,
            formatting.format_settings_notice("Отправь JSON обычным текстовым сообщением."),
            keyboards.hide_keyboard(),
        )
        await _delete_user_message(message)
        return

    try:
        write_prompt_json(raw, prompt_examples_path)
    except PromptJsonError as exc:
        await _edit_menu_from_state(
            message,
            state,
            formatting.format_settings_notice(f"{exc}\n\nОтправь исправленный JSON ещё раз."),
            keyboards.hide_keyboard(),
        )
        await _delete_user_message(message)
        return

    if bid_generator is not None:
        bid_generator.reload_prompt()
    await _delete_user_message(message)
    await _edit_menu_from_state(
        message,
        state,
        formatting.format_settings_notice("JSON промта обновлён."),
        keyboards.hide_keyboard(),
    )
    await state.clear()


@router.callback_query(F.data.startswith(keyboards.CALLBACK_LIST_PREFIX))
async def handle_list_page(callback: CallbackQuery, settings: Settings, store: StateStore) -> None:
    filter_key, page = _parse_list_data(callback.data or "")
    projects = await store.recent_projects()
    # With AI off there's no primary check, so nothing is ever marked passed —
    # fall back to showing everything (passed_ids=None disables the filter).
    passed = await store.passed_ids() if settings.ai_active else None
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
    except BidGenerationError as exc:
        log.exception("generate failed for project %s: %s", project_id, exc)
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
    except BidGenerationError as exc:
        log.exception("regen failed for project %s: %s", project_id, exc)
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


async def _send_settings_flow_message(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    parse_mode: str | None = "HTML",
) -> bool:
    try:
        await callback.message.answer(text, reply_markup=markup, parse_mode=parse_mode)
    except TelegramAPIError:
        log.exception("failed to send settings flow message")
        return False
    return True


async def _ensure_callback_allowed(callback: CallbackQuery, settings: Settings) -> bool:
    if _is_allowed_chat(callback.message.chat.id, settings):
        return True
    await callback.answer("Нет доступа к настройкам", show_alert=True)
    return False


def _is_allowed_chat(chat_id: int, settings: Settings) -> bool:
    return str(chat_id) == str(settings.telegram_chat_id)


async def _safe_edit(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None,
    *,
    parse_mode: str | None = "HTML",
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        log.exception("editMessageText failed")


async def _remember_menu_message(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(
        menu_chat_id=callback.message.chat.id,
        menu_message_id=callback.message.message_id,
    )


async def _edit_menu_from_state(
    message: Message,
    state: FSMContext,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    parse_mode: str | None = "HTML",
) -> None:
    data = await state.get_data()
    chat_id = data.get("menu_chat_id")
    message_id = data.get("menu_message_id")
    if chat_id is not None and message_id is not None:
        try:
            await message.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
                reply_markup=markup,
                parse_mode=parse_mode,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            log.exception("editMessageText failed for settings flow")
    try:
        await message.answer(text, reply_markup=markup, parse_mode=parse_mode)
    except TelegramAPIError:
        log.exception("failed to send settings flow message")


async def _delete_user_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramAPIError:
        log.exception("failed to delete user settings message")


def _parse_skill_id_suffix(data: str, prefix: str) -> int:
    try:
        return int(data[len(prefix):])
    except ValueError:
        return 0


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


def _find_category(settings: Settings, skill_id: int):
    return next((category for category in settings.categories if category.skill_id == skill_id), None)


def _normalize_category_name(raw: str) -> str:
    return " ".join(raw.split())
