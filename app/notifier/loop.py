import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.config import Settings
from app.gemini import BidGenerator, BidGenerationError
from app.projects import Project
from app.source import FreelancehuntSource
from app.storage import StateStore
from app.telegram import formatting, keyboards

log = logging.getLogger(__name__)


class NotifierLoop:
    def __init__(
        self,
        bot: Bot,
        store: StateStore,
        source: FreelancehuntSource,
        settings: Settings,
        bid_generator: BidGenerator | None = None,
    ) -> None:
        self._bot = bot
        self._store = store
        self._source = source
        self._settings = settings
        self._bid_generator = bid_generator

    async def run(self, stop_event: asyncio.Event) -> None:
        log.info("starting notifier loop, interval=%ss", self._settings.poll_interval)
        while not stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("notifier tick failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._settings.poll_interval)
            except asyncio.TimeoutError:
                pass
        log.info("notifier loop stopped")

    async def _tick(self) -> None:
        projects = await self._source.fetch_projects()
        if not projects:
            return

        await self._store.add_projects(projects)
        watermark = self._store.last_published_ts
        first_run = not self._store.initialized
        latest_ts = max(p.published_ts for p in projects)

        if first_run:
            if self._settings.send_existing_on_first_run:
                log.info("first run: sending %d existing projects", len(projects))
                await self._send_batch(sorted(projects, key=lambda p: p.published_ts))
            else:
                log.info(
                    "first run: setting watermark to %d, suppressing %d existing projects",
                    latest_ts, len(projects),
                )
            await self._store.mark_seen([p.id for p in projects])
            await self._store.update_last_published_ts(latest_ts)
            await self._store.mark_initialized()
            return

        new_projects = []
        for p in projects:
            if p.published_ts > watermark and not await self._store.is_seen(p.id):
                new_projects.append(p)
        if not new_projects:
            return

        new_projects.sort(key=lambda p: p.published_ts)
        log.info(
            "found %d new projects (watermark=%d, fetched=%d)",
            len(new_projects), watermark, len(projects),
        )
        sent = await self._send_batch(new_projects)
        if sent:
            await self._store.update_last_published_ts(max(p.published_ts for p in sent))

    async def _send_batch(self, projects: list[Project]) -> list[Project]:
        sent: list[Project] = []
        for project in projects:
            if not await self._send_project(project):
                break
            sent.append(project)
            await asyncio.sleep(1)
        if sent:
            await self._store.mark_seen([p.id for p in sent])
        return sent

    async def _send_project(self, project: Project) -> bool:
        text = formatting.format_project_notification(
            project, self._settings.category_name, self._settings.listing_url
        )
        try:
            order_message = await self._bot.send_message(
                chat_id=self._settings.telegram_chat_id,
                text=text,
                reply_markup=keyboards.notification_keyboard(),
            )
        except TelegramAPIError:
            log.exception("failed to send project %s", project.id)
            return False
        log.info("sent project %s: %s", project.id, project.title)

        if self._bid_generator is not None:
            await self._send_bid_reply(project, order_message.message_id)
        return True

    async def _send_bid_reply(self, project: Project, reply_to: int) -> None:
        assert self._bid_generator is not None
        try:
            bid_text = await self._bid_generator.generate(project)
        except BidGenerationError:
            log.exception("bid generation failed for project %s", project.id)
            return
        try:
            await self._bot.send_message(
                chat_id=self._settings.telegram_chat_id,
                text=bid_text,
                reply_to_message_id=reply_to,
                reply_markup=keyboards.regen_bid_keyboard(project.id),
                parse_mode=None,
            )
        except TelegramAPIError:
            log.exception("failed to send bid for project %s", project.id)
