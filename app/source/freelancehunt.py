import logging
from datetime import datetime, timezone

import requests

from app.projects import Project

log = logging.getLogger(__name__)

API_BASE = "https://api.freelancehunt.com/v2"
WEB_PROJECT_URL = "https://freelancehunt.com/project/{id}"


class FreelancehuntSource:
    """Fetches open projects in a given skill from the FreelanceHunt API.

    Docs: https://apidocs.freelancehunt.com/
    """

    def __init__(
        self,
        token: str,
        skill_id: int,
        page_size: int = 20,
        timeout: int = 30,
    ) -> None:
        self._token = token
        self._skill_id = skill_id
        self._page_size = page_size
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "freelancehunt-notifier/1.0 (+telegram-bot)",
            }
        )

    def fetch_projects(self) -> list[Project]:
        params = {
            "filter[skill_id]": self._skill_id,
            "page[size]": self._page_size,
        }
        resp = self._session.get(f"{API_BASE}/projects", params=params, timeout=self._timeout)
        if not resp.ok:
            raise RuntimeError(f"FreelanceHunt API {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data", [])
        result: list[Project] = []
        for item in data:
            project = _to_project(item)
            if project is not None:
                result.append(project)
        return result


def _to_project(item: dict) -> Project | None:
    try:
        project_id = str(item["id"])
        attrs = item.get("attributes", {})
        name = (attrs.get("name") or "").strip()
        if not name:
            return None

        description = (attrs.get("description") or "").strip()
        budget = _format_budget(attrs.get("budget"))

        published_ts, absolute_time = _format_published(attrs.get("published_at"))
        relative_time = _format_relative(published_ts) if published_ts else ""

        return Project(
            id=project_id,
            url=WEB_PROJECT_URL.format(id=project_id),
            title=name,
            budget=budget,
            description=description,
            relative_time=relative_time,
            absolute_time=absolute_time,
            published_ts=published_ts,
        )
    except Exception:
        log.exception("failed to parse project item: %s", item)
        return None


def _format_budget(budget: dict | None) -> str:
    if not budget:
        return ""
    amount = budget.get("amount")
    currency = budget.get("currency") or ""
    if amount is None:
        return ""
    return f"{amount} {currency}".strip()


def _format_published(raw: str | None) -> tuple[int, str]:
    if not raw:
        return 0, ""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return 0, ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp()), _format_absolute(dt)


_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _format_absolute(dt: datetime) -> str:
    return f"{dt.day} {_MONTHS_RU[dt.month]}, {dt.hour:02d}:{dt.minute:02d}"


def _format_relative(ts: int) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    diff = max(0, now - ts)
    if diff < 60:
        return "только что"
    if diff < 3600:
        return _plural(diff // 60, "минуту", "минуты", "минут") + " назад"
    if diff < 86400:
        return _plural(diff // 3600, "час", "часа", "часов") + " назад"
    return _plural(diff // 86400, "день", "дня", "дней") + " назад"


def _plural(n: int, one: str, few: str, many: str) -> str:
    mod10 = n % 10
    mod100 = n % 100
    if mod10 == 1 and mod100 != 11:
        word = one
    elif 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"
