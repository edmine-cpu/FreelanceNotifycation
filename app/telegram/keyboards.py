from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.projects import Category, Project

CALLBACK_LIST_PREFIX = "list:"
CALLBACK_START = "start"
CALLBACK_NOOP = "noop"
CALLBACK_REGEN_PREFIX = "regen:"
CALLBACK_HIDE = "hide"
CALLBACK_SHOW_PREFIX = "show:"
CALLBACK_GEN_PREFIX = "gen:"

# Filter key used in `list:<filter>:<page>` callbacks to mean "every category".
LIST_FILTER_ALL = "all"

MAX_BUTTON_TEXT = 60

HIDE_BUTTON_TEXT = "🙈 Скрыть"


def list_callback(filter_key: str, page: int) -> str:
    return f"{CALLBACK_LIST_PREFIX}{filter_key}:{page}"


def start_menu_keyboard(categories: list[Category]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"📂 {_truncate(category.name)}",
                callback_data=list_callback(str(category.skill_id), 0),
            )
        ]
        for category in categories
    ]
    rows.append(
        [InlineKeyboardButton(text="📋 Все", callback_data=list_callback(LIST_FILTER_ALL, 0))]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def projects_page_keyboard(
    projects: list[Project],
    page: int,
    page_size: int,
    filter_key: str,
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

    prev_cb = list_callback(filter_key, page - 1) if page > 0 else CALLBACK_NOOP
    next_cb = list_callback(filter_key, page + 1) if page < total_pages - 1 else CALLBACK_NOOP
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
