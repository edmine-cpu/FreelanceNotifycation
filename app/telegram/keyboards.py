from app.projects import Project

CALLBACK_OPEN_LIST = "list:0"
CALLBACK_LIST_PREFIX = "list:"
CALLBACK_START = "start"
CALLBACK_NOOP = "noop"

MAX_BUTTON_TEXT = 60


def start_menu_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "📋 Последние проекты", "callback_data": CALLBACK_OPEN_LIST}],
        ],
    }


def projects_page_keyboard(
    projects: list[Project],
    page: int,
    page_size: int,
) -> dict:
    total = len(projects)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    chunk = projects[start : start + page_size]

    rows: list[list[dict]] = [
        [{"text": _truncate(p.title), "url": p.url}] for p in chunk
    ]

    prev_cb = f"{CALLBACK_LIST_PREFIX}{page - 1}" if page > 0 else CALLBACK_NOOP
    next_cb = f"{CALLBACK_LIST_PREFIX}{page + 1}" if page < total_pages - 1 else CALLBACK_NOOP
    rows.append(
        [
            {"text": "« назад" if page > 0 else "·", "callback_data": prev_cb},
            {"text": f"{page + 1}/{total_pages}", "callback_data": CALLBACK_NOOP},
            {"text": "далее »" if page < total_pages - 1 else "·", "callback_data": next_cb},
        ]
    )
    rows.append([{"text": "🏠 В меню", "callback_data": CALLBACK_START}])
    return {"inline_keyboard": rows}


def empty_history_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🏠 В меню", "callback_data": CALLBACK_START}],
        ],
    }


def _truncate(text: str) -> str:
    if len(text) <= MAX_BUTTON_TEXT:
        return text
    return text[: MAX_BUTTON_TEXT - 1].rstrip() + "…"
