from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.projects import Category, Project

CALLBACK_LIST_PREFIX = "list:"
CALLBACK_RAW_PREFIX = "raw:"
CALLBACK_START = "start"
CALLBACK_NOOP = "noop"
CALLBACK_REGEN_PREFIX = "regen:"
CALLBACK_HIDE = "hide"
CALLBACK_SHOW_PREFIX = "show:"
CALLBACK_GEN_PREFIX = "gen:"
CALLBACK_SETTINGS = "settings"
CALLBACK_ADD_CATEGORY = "settings:add_category"
CALLBACK_PROMPT_JSON = "settings:prompt_json"
CALLBACK_PROMPT_EDIT = "settings:prompt_edit"
CALLBACK_NOTIFICATIONS = "settings:notifications"
CALLBACK_TOGGLE_MUTE_PREFIX = "settings:toggle_mute:"

# Filter key used in `list:<filter>:<page>` callbacks to mean "every category".
LIST_FILTER_ALL = "all"

MAX_BUTTON_TEXT = 60

HIDE_BUTTON_TEXT = "🙈 Скрыть"


def list_callback(filter_key: str, page: int) -> str:
    return f"{CALLBACK_LIST_PREFIX}{filter_key}:{page}"


def raw_callback(skill_id: int, page: int) -> str:
    return f"{CALLBACK_RAW_PREFIX}{skill_id}:{page}"


def start_menu_keyboard(categories: list[Category]) -> InlineKeyboardMarkup:
    # Per category, two buttons in one row: 📂 = filtered (passed the primary
    # check, from cache), 🔴 = unfiltered (live fetch, everything).
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"📂 {_truncate(category.name)}",
                callback_data=list_callback(str(category.skill_id), 0),
            ),
            InlineKeyboardButton(
                text=f"🔴 {_truncate(category.name)}",
                callback_data=raw_callback(category.skill_id, 0),
            ),
        ]
        for category in categories
    ]
    rows.append(
        [InlineKeyboardButton(text="📋 Все", callback_data=list_callback(LIST_FILTER_ALL, 0))]
    )
    rows.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data=CALLBACK_SETTINGS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить новую категорию по ID", callback_data=CALLBACK_ADD_CATEGORY)],
            [InlineKeyboardButton(text="📝 Изменить промт", callback_data=CALLBACK_PROMPT_JSON)],
            [InlineKeyboardButton(text="🔔 Уведомления категорий", callback_data=CALLBACK_NOTIFICATIONS)],
            [InlineKeyboardButton(text="🏠 В меню", callback_data=CALLBACK_START)],
        ]
    )


def category_notifications_keyboard(
    categories: list[Category],
    muted_skill_ids: set[int],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for category in categories:
        muted = category.skill_id in muted_skill_ids
        icon = "🔕" if muted else "🔔"
        status = "выкл" if muted else "вкл"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {status} · {_truncate(category.name)}",
                    callback_data=f"{CALLBACK_TOGGLE_MUTE_PREFIX}{category.skill_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Настройки", callback_data=CALLBACK_SETTINGS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prompt_json_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить JSON", callback_data=CALLBACK_PROMPT_EDIT)],
            [InlineKeyboardButton(text=HIDE_BUTTON_TEXT, callback_data=CALLBACK_HIDE)],
        ]
    )


def settings_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Настройки", callback_data=CALLBACK_SETTINGS)]]
    )


def projects_page_keyboard(
    projects: list[Project],
    page: int,
    page_size: int,
    filter_key: str,
    raw: bool = False,
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

    # In raw mode filter_key is the skill_id; navigation re-enters the live view.
    def nav(target_page: int) -> str:
        return raw_callback(int(filter_key), target_page) if raw else list_callback(filter_key, target_page)

    prev_cb = nav(page - 1) if page > 0 else CALLBACK_NOOP
    next_cb = nav(page + 1) if page < total_pages - 1 else CALLBACK_NOOP
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


def notification_keyboard(project_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Сгенерировать ответ", callback_data=f"{CALLBACK_GEN_PREFIX}{project_id}")],
            [InlineKeyboardButton(text=HIDE_BUTTON_TEXT, callback_data=CALLBACK_HIDE)],
        ]
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
