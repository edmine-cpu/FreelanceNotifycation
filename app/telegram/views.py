from aiogram.types import InlineKeyboardMarkup

from app.config import Settings
from app.projects import Project

from . import formatting, keyboards


def start_view(settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    return formatting.format_start_menu(settings.category_label), keyboards.start_menu_keyboard()


def projects_page_view(
    projects: list[Project],
    page: int,
    settings: Settings,
) -> tuple[str, InlineKeyboardMarkup]:
    if not projects:
        return (
            formatting.format_empty_history(settings.category_label),
            keyboards.empty_history_keyboard(),
        )

    page_size = settings.page_size
    total = len(projects)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    text = formatting.format_projects_page_header(
        settings.category_label, page, total_pages, total
    )
    markup = keyboards.projects_page_keyboard(projects, page, page_size)
    return text, markup
