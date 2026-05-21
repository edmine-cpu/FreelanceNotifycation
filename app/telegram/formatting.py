import html

from app.projects import Project

MAX_DESC = 300


def format_project_notification(project: Project, category_name: str, listing_url: str) -> str:
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

    parts.append(f'📂 <a href="{html.escape(listing_url, quote=True)}">{html.escape(category_name)}</a>')
    return "\n\n".join(parts)


def format_start_menu(category_name: str) -> str:
    return (
        f"Привет! Это бот для категории <b>{html.escape(category_name)}</b> на FreelanceHunt.\n\n"
        "Я раз в минуту проверяю новые проекты и присылаю уведомления.\n"
        "Можешь посмотреть последние из истории кнопкой ниже."
    )


def format_projects_page_header(category_name: str, page: int, total_pages: int, total: int) -> str:
    return (
        f"<b>Последние проекты</b> — {html.escape(category_name)}\n"
        f"Всего: {total} · страница {page + 1}/{max(total_pages, 1)}"
    )


def format_empty_history(category_name: str) -> str:
    return (
        f"Пока в истории нет проектов из категории <b>{html.escape(category_name)}</b>.\n"
        "Подожди до следующей проверки — и они появятся здесь."
    )
