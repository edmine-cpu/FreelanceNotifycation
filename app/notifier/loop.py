import logging
import threading
import time

from app.config import Settings
from app.projects import Project
from app.source import FreelancehuntSource
from app.storage import StateStore
from app.telegram import formatting
from app.telegram.client import TelegramClient, TelegramError

log = logging.getLogger(__name__)


class NotifierLoop:
    def __init__(
        self,
        client: TelegramClient,
        store: StateStore,
        source: FreelancehuntSource,
        settings: Settings,
    ) -> None:
        self._client = client
        self._store = store
        self._source = source
        self._settings = settings

    def run(self, stop_event: threading.Event) -> None:
        log.info("starting notifier loop, interval=%ss", self._settings.poll_interval)
        while not stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("notifier tick failed")
            stop_event.wait(self._settings.poll_interval)
        log.info("notifier loop stopped")

    def _tick(self) -> None:
        projects = self._source.fetch_projects()
        if not projects:
            return

        self._store.add_projects(projects)
        first_run = not self._store.initialized

        new_projects = [p for p in projects if not self._store.is_seen(p.id)]
        new_projects.sort(key=lambda p: p.published_ts)

        if first_run and not self._settings.send_existing_on_first_run:
            log.info("first run: marking %d existing projects as seen without sending", len(new_projects))
            self._store.mark_seen([p.id for p in projects])
            self._store.mark_initialized()
            return

        sent_ids: list[str] = []
        for project in new_projects:
            if self._send_project(project):
                sent_ids.append(project.id)
                time.sleep(1)
            else:
                break

        if sent_ids:
            self._store.mark_seen(sent_ids)
        if not self._store.initialized:
            self._store.mark_initialized()

    def _send_project(self, project: Project) -> bool:
        text = formatting.format_project_notification(
            project, self._settings.category_name, self._settings.listing_url
        )
        try:
            self._client.send_message(self._settings.telegram_chat_id, text)
        except TelegramError:
            log.exception("failed to send project %s", project.id)
            return False
        log.info("sent project %s: %s", project.id, project.title)
        return True
