import html

from app.projects import Project

MAX_DESC = 300


def format_project_notification(project: Project) -> str:
    parts = [f'🆕 <a href="{html.escape(project.url, quote=True)}"><b>{html.escape(project.title)}</b></a>']

    if project.budget:
        parts.append(f"💰 <b>{html.escape(project.budget)}</b>")

    if project.description:
        snippet = project.description
        if len(snippet) > MAX_DESC:
            snippet = snippet[:MAX_DESC].rstrip() + "…"
        parts.append(html.escape(snippet))

    time_bits: list[str] = []
    if project.relative_time:
        time_bits.append(html.escape(project.relative_time))
    if project.absolute_time and project.absolute_time != project.relative_time:
        time_bits.append(f"({html.escape(project.absolute_time)})")
    if time_bits:
        parts.append("🕒 " + " ".join(time_bits))

    if project.category_name:
        category = html.escape(project.category_name)
        if project.category_url:
            category = f'<a href="{html.escape(project.category_url, quote=True)}">{category}</a>'
        parts.append(f"📂 {category}")
    return "\n\n".join(parts)


def format_start_menu(category_label: str) -> str:
    return (
        f"Привет! Это бот для категорий <b>{html.escape(category_label)}</b> на FreelanceHunt.\n\n"
        "Я раз в минуту проверяю новые проекты и присылаю уведомления.\n"
        "Можешь посмотреть последние из истории кнопкой ниже."
    )


def format_settings_menu(category_label: str, muted_count: int) -> str:
    muted_line = f"\nОтключено уведомлений: <b>{muted_count}</b>" if muted_count else ""
    return f"<b>Настройки:</b>\n\nКатегории: {html.escape(category_label)}{muted_line}"


def format_category_notifications(category_label: str) -> str:
    return (
        "<b>Уведомления категорий</b>\n\n"
        f"Категории: {html.escape(category_label)}\n"
        "Нажми на категорию, чтобы включить или отключить уведомления."
    )


def format_category_names(category_label: str) -> str:
    return (
        "<b>Имена категорий</b>\n\n"
        f"Категории: {html.escape(category_label)}\n"
        "Нажми на категорию, чтобы задать или изменить имя."
    )


def format_add_category_prompt() -> str:
    return (
        "<b>Добавить новую категорию</b>\n\n"
        "Отправь ID категории FreelanceHunt одним сообщением."
    )


def format_category_name_prompt(skill_id: int, current_name: str) -> str:
    return (
        "<b>Имя категории</b>\n\n"
        f"ID: <code>{skill_id}</code>\n"
        f"Сейчас: <b>{html.escape(current_name)}</b>\n\n"
        "Отправь новое имя одним сообщением."
    )


def format_prompt_edit_prompt() -> str:
    return (
        "<b>Изменить промт</b>\n\n"
        "Отправь новый JSON целиком одним сообщением."
    )


def format_settings_notice(text: str) -> str:
    return f"<b>Настройки:</b>\n\n{html.escape(text)}"


def format_projects_page_header(category_label: str, page: int, total_pages: int, total: int) -> str:
    return (
        f"<b>Последние проекты</b> — {html.escape(category_label)}\n"
        f"Всего: {total} · страница {page + 1}/{max(total_pages, 1)}"
    )


def format_empty_history(category_label: str) -> str:
    return (
        f"Пока в истории нет проектов из категорий <b>{html.escape(category_label)}</b>.\n"
        "Подожди до следующей проверки — и они появятся здесь."
    )
