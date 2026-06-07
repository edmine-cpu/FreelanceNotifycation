from aiogram.types import InlineKeyboardMarkup

from app.config import Settings
from app.projects import Project

from . import formatting, keyboards


def start_view(settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    return (
        formatting.format_start_menu(settings.category_label),
        keyboards.start_menu_keyboard(settings.categories),
    )


def settings_view(settings: Settings, muted_skill_ids: set[int]) -> tuple[str, InlineKeyboardMarkup]:
    return (
        formatting.format_settings_menu(settings.category_label, len(muted_skill_ids)),
        keyboards.settings_keyboard(),
    )


def category_notifications_view(
    settings: Settings,
    muted_skill_ids: set[int],
) -> tuple[str, InlineKeyboardMarkup]:
    return (
        formatting.format_category_notifications(settings.category_label),
        keyboards.category_notifications_keyboard(settings.categories, muted_skill_ids),
    )


def category_names_view(settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    return (
        formatting.format_category_names(settings.category_label),
        keyboards.category_names_keyboard(settings.categories),
    )


def projects_page_view(
    projects: list[Project],
    page: int,
    settings: Settings,
    filter_key: str = keyboards.LIST_FILTER_ALL,
    *,
    passed_ids: set[str] | None = None,
    unfiltered: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    label = _filter_label(settings, filter_key, unfiltered)
    filtered = _filter_projects(projects, filter_key)
    # Filtered (📂) view: keep only orders that passed the primary check.
    # Unfiltered (🔴) view: show everything as-is.
    if not unfiltered and passed_ids is not None:
        filtered = [p for p in filtered if p.id in passed_ids]

    if not filtered:
        return (
            formatting.format_empty_history(label),
            keyboards.empty_history_keyboard(),
        )

    page_size = settings.page_size
    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    text = formatting.format_projects_page_header(label, page, total_pages, total)
    markup = keyboards.projects_page_keyboard(filtered, page, page_size, filter_key, raw=unfiltered)
    return text, markup


def _filter_projects(projects: list[Project], filter_key: str) -> list[Project]:
    if filter_key == keyboards.LIST_FILTER_ALL:
        return projects
    try:
        skill_id = int(filter_key)
    except ValueError:
        return projects
    return [p for p in projects if p.skill_id == skill_id]


def _filter_label(settings: Settings, filter_key: str, unfiltered: bool = False) -> str:
    if filter_key == keyboards.LIST_FILTER_ALL:
        return "Все категории"
    names = {str(category.skill_id): category.name for category in settings.categories}
    name = names.get(filter_key, f"Категория #{filter_key}")
    return f"{name} · все" if unfiltered else name
