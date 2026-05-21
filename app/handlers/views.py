"""Shared view builders: text + keyboard pairs used by commands and callbacks."""

from app.config import Settings
from app.projects import Project
from app.telegram import formatting, keyboards


def start_view(settings: Settings) -> tuple[str, dict]:
    return formatting.format_start_menu(settings.category_name), keyboards.start_menu_keyboard()


def projects_page_view(
    projects: list[Project],
    page: int,
    settings: Settings,
) -> tuple[str, dict]:
    if not projects:
        return (
            formatting.format_empty_history(settings.category_name),
            keyboards.empty_history_keyboard(),
        )

    page_size = settings.page_size
    total = len(projects)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    text = formatting.format_projects_page_header(
        settings.category_name, page, total_pages, total
    )
    markup = keyboards.projects_page_keyboard(projects, page, page_size)
    return text, markup
