from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.projects import Project

CALLBACK_OPEN_LIST = "list:0"
CALLBACK_LIST_PREFIX = "list:"
CALLBACK_START = "start"
CALLBACK_NOOP = "noop"
CALLBACK_REGEN_PREFIX = "regen:"
CALLBACK_HIDE = "hide"
CALLBACK_SHOW_PREFIX = "show:"
CALLBACK_GEN_PREFIX = "gen:"

MAX_BUTTON_TEXT = 60

HIDE_BUTTON_TEXT = "🙈 Скрыть"


def start_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Последние проекты", callback_data=CALLBACK_OPEN_LIST)
    return builder.as_markup()


def projects_page_keyboard(
    projects: list[Project],
    page: int,
    page_size: int,
) -> InlineKeyboardMarkup:
    total = len(projects)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    chunk = projects[start : start + page_size]

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=_truncate(p.title), callback_data=f"{CALLBACK_SHOW_PREFIX}{p.id}")]
        for p in chunk
    ]

    prev_cb = f"{CALLBACK_LIST_PREFIX}{page - 1}" if page > 0 else CALLBACK_NOOP
    next_cb = f"{CALLBACK_LIST_PREFIX}{page + 1}" if page < total_pages - 1 else CALLBACK_NOOP
    rows.append(
        [
            InlineKeyboardButton(text="« назад" if page > 0 else "·", callback_data=prev_cb),
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data=CALLBACK_NOOP),
            InlineKeyboardButton(text="далее »" if page < total_pages - 1 else "·", callback_data=next_cb),
        ]
    )
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data=CALLBACK_START)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def empty_history_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 В меню", callback_data=CALLBACK_START)]]
    )


def project_detail_keyboard(project: Project) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть проект", url=project.url)],
            [InlineKeyboardButton(text="✍️ Сгенерировать ответ", callback_data=f"{CALLBACK_GEN_PREFIX}{project.id}")],
            [InlineKeyboardButton(text=HIDE_BUTTON_TEXT, callback_data=CALLBACK_HIDE)],
        ]
    )


def notification_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=HIDE_BUTTON_TEXT, callback_data=CALLBACK_HIDE)]]
    )


def regen_bid_keyboard(project_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Перегенерировать", callback_data=f"{CALLBACK_REGEN_PREFIX}{project_id}")],
            [InlineKeyboardButton(text=HIDE_BUTTON_TEXT, callback_data=CALLBACK_HIDE)],
        ]
    )


def hide_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=HIDE_BUTTON_TEXT, callback_data=CALLBACK_HIDE)]]
    )


def _truncate(text: str) -> str:
    if len(text) <= MAX_BUTTON_TEXT:
        return text
    return text[: MAX_BUTTON_TEXT - 1].rstrip() + "…"
