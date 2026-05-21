import logging
import re

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from app.projects import Project

log = logging.getLogger(__name__)

_PROJECT_ID_RE = re.compile(r"/project/[^/]+/(\d+)\.html")


class FreelancehuntParser:
    def __init__(
        self,
        listing_url: str,
        user_agent: str,
        timeout: int = 30,
        impersonate: str = "chrome",
    ) -> None:
        self._url = listing_url
        self._user_agent = user_agent
        self._timeout = timeout
        self._impersonate = impersonate

    def fetch_projects(self) -> list[Project]:
        html_text = self._fetch_html()
        return self._parse(html_text)

    def _fetch_html(self) -> str:
        resp = curl_requests.get(
            self._url,
            headers={
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ru,en;q=0.9",
                "Upgrade-Insecure-Requests": "1",
            },
            impersonate=self._impersonate,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _parse(html_text: str) -> list[Project]:
        soup = BeautifulSoup(html_text, "html.parser")
        table = soup.find("table", class_="project-list")
        if table is None:
            log.warning("project-list table not found; layout may have changed")
            return []

        result: list[Project] = []
        for row in table.select("tbody > tr[data-published]"):
            project = _row_to_project(row)
            if project is not None:
                result.append(project)
        return result


def _row_to_project(row) -> Project | None:
    link = row.select_one("h2 a")
    if not link or not link.get("href"):
        return None
    url = link["href"]
    match = _PROJECT_ID_RE.search(url)
    if not match:
        return None

    price_el = row.select_one("td.project-budget div.price") or row.select_one("div.price")
    budget = price_el.get_text(strip=True) if price_el else ""

    desc_el = row.select_one("td.left p")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""

    time_el = row.select_one("span.with-tooltip")
    relative_time = time_el.get_text(strip=True) if time_el else ""
    absolute_time = time_el.get("title", "").strip() if time_el else ""

    try:
        published_ts = int(row.get("data-published", "0"))
    except ValueError:
        published_ts = 0

    return Project(
        id=match.group(1),
        url=url,
        title=link.get_text(strip=True),
        budget=budget,
        description=description,
        relative_time=relative_time,
        absolute_time=absolute_time,
        published_ts=published_ts,
    )
