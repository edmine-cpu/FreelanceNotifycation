import asyncio
import logging
import time
from dataclasses import replace

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from app.ai import BidGenerator, OrderScreener
from app.ai.policy import ai_allowed
from app.config import Settings, build_category, parse_skill_ids
from app.projects import Project
from app.source import FreelancehuntSource
from app.storage import StateStore
from app.telegram import formatting, keyboards

log = logging.getLogger(__name__)


def _group_by_skill(projects: list[Project]) -> dict[int, list[Project]]:
    grouped: dict[int, list[Project]] = {}
    for project in projects:
        grouped.setdefault(project.skill_id, []).append(project)
    return grouped


def _dedupe_by_id(projects: list[Project]) -> list[Project]:
    """Drop repeated projects (the same order can match several watched skills
    and arrive once per category query), keeping the first occurrence."""
    seen: set[str] = set()
    result: list[Project] = []
    for project in projects:
        if project.id not in seen:
            seen.add(project.id)
            result.append(project)
    return result


class NotifierLoop:
    def __init__(
        self,
        bot: Bot,
        store: StateStore,
        source: FreelancehuntSource,
        settings: Settings,
        screener: OrderScreener | None = None,
        bid_generator: BidGenerator | None = None,
        user_id: int | None = None,
    ) -> None:
        self._bot = bot
        self._store = store
        self._source = source
        self._settings = settings
        self._screener = screener if ai_allowed(user_id) else None
        self._ai_allowed = ai_allowed(user_id)
        # project id -> note for orders the primary check marked "maybe".
        self._screen_notes: dict[str, str] = {}

    async def run(self, stop_event: asyncio.Event) -> None:
        log.info("starting notifier loop, interval=%ss", self._settings.poll_interval)
        await self.rebuild_filter()
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

    async def rebuild_filter(self) -> None:
        """Re-run the primary check over all currently open projects and rebuild
        the filtered (📂) set from scratch. Runs once on each bot launch, before
        the polling loop. Sends no notifications — it only repopulates passed_ids.
        No-op when AI is off (then 📂 simply shows everything via an empty set)."""
        if self._screener is None:
            return
        try:
            projects = await self._source.fetch_projects()
        except Exception:
            log.exception("filter rebuild: fetch failed, keeping previous filtered set")
            return
        if projects:
            await self._store.add_projects(projects)
        # Re-screen everything the /start lists can show, not only open orders,
        # so the 📂 view follows the current prompt.
        projects = await self._store.recent_projects()
        log.info("filter rebuild: screening %d projects (once at startup)…", len(projects))
        started = time.monotonic()
        slots = asyncio.Semaphore(8)

        async def allowed(project: Project) -> bool:
            async with slots:
                return (await self._screener.screen(project)).allowed

        verdicts = await asyncio.gather(*(allowed(project) for project in projects))
        passed = [project.id for project, ok in zip(projects, verdicts) if ok]
        await self._store.set_passed(passed)
        log.info(
            "filter rebuild done in %.1fs: %d/%d projects passed the primary check",
            time.monotonic() - started, len(passed), len(projects),
        )

    async def _tick(
        self,
        projects: list[Project] | None = None,
        fetched_skill_ids: set[int] | None = None,
    ) -> None:
        if projects is None:
            projects = await self._source.fetch_projects()
        # An empty successful response still initializes the subscription, so
        # its first future order is delivered. Failed requests never seed it.
        for skill_id in (fetched_skill_ids or set()) - {p.skill_id for p in projects}:
            await self._store.update_last_published_ts(skill_id, 0)
        if not projects:
            return

        await self._store.add_projects(projects)
        by_category = _group_by_skill(projects)
        muted_skill_ids = await self._store.muted_skill_ids()

        new_projects: list[Project] = []
        first_seen: list[tuple[int, list[Project]]] = []
        muted_groups: list[tuple[int, list[Project]]] = []
        for skill_id, group in by_category.items():
            if skill_id in muted_skill_ids:
                muted_groups.append((skill_id, group))
                continue
            if not self._store.has_watermark(skill_id):
                first_seen.append((skill_id, group))
                continue
            watermark = self._store.last_published_ts(skill_id)
            for p in group:
                # published_ts == 0 means the date failed to parse; fall back to
                # the is_seen guard so such projects are still announced once.
                is_new = p.published_ts >= watermark or p.published_ts == 0
                if is_new and not await self._store.is_seen(p.id):
                    new_projects.append(p)

        pending_ids = {project.id for project in new_projects}
        if self._settings.send_existing_on_first_run:
            pending_ids.update(project.id for _, group in first_seen for project in group)
        for skill_id, group in muted_groups:
            await self._skip_muted_category(skill_id, group, exclude_ids=pending_ids)
        for skill_id, group in first_seen:
            await self._init_category(skill_id, group, exclude_ids=pending_ids)

        new_projects = _dedupe_by_id(new_projects)
        if not new_projects:
            return

        new_projects.sort(key=lambda p: p.published_ts)
        log.info("found %d new projects (fetched=%d)", len(new_projects), len(projects))
        processed = await self._send_batch(new_projects)
        await self._advance_watermarks(processed)

    async def _init_category(
        self, skill_id: int, group: list[Project], exclude_ids: set[str] | None = None
    ) -> None:
        """Handle the first tick that ever sees a category: suppress (or, if
        configured, send) its current backlog, then record its watermark so
        later ticks only pick up genuinely new projects."""
        if self._settings.send_existing_on_first_run:
            log.info("first sight of skill %d: sending %d existing projects", skill_id, len(group))
            processed = await self._send_batch(sorted(group, key=lambda p: p.published_ts))
            await self._advance_watermarks(processed)
            return
        else:
            log.info("first sight of skill %d: suppressing %d existing projects", skill_id, len(group))
        maximum_ts = max(p.published_ts for p in group)
        await self._store.mark_seen([
            p.id for p in group
            if p.published_ts in (0, maximum_ts) and p.id not in (exclude_ids or set())
        ])
        await self._store.update_last_published_ts(
            skill_id, maximum_ts
        )

    async def _skip_muted_category(
        self, skill_id: int, group: list[Project], exclude_ids: set[str] | None = None
    ) -> None:
        """Keep muted categories up to date without sending notifications."""
        if not group:
            return
        log.info("skill %d is muted: suppressing %d fetched projects", skill_id, len(group))
        maximum_ts = max(p.published_ts for p in group)
        await self._store.mark_seen([
            p.id for p in group
            if p.published_ts in (0, maximum_ts) and p.id not in (exclude_ids or set())
        ])
        await self._store.update_last_published_ts(
            skill_id, maximum_ts
        )

    async def _advance_watermarks(self, sent: list[Project]) -> None:
        subscribed = await self._store.skill_ids(parse_skill_ids(self._settings.skill_ids))
        for skill_id, group in _group_by_skill(sent).items():
            if skill_id not in subscribed:
                continue
            await self._store.update_last_published_ts(
                skill_id, max(p.published_ts for p in group)
            )

    async def _current_delivery_project(self, project: Project) -> Project | None:
        """Recheck preferences after each potentially slow operation."""
        subscribed = await self._store.skill_ids(parse_skill_ids(self._settings.skill_ids))
        if project.skill_id not in subscribed:
            return None
        if project.skill_id in await self._store.muted_skill_ids():
            # Suppress this category's backlog without marking a shared project
            # seen for any other category that is still enabled.
            await self._store.update_last_published_ts(project.skill_id, project.published_ts)
            return None
        names = await self._store.category_names()
        return replace(project, category_name=build_category(project.skill_id, names).name)

    async def _send_batch(self, projects: list[Project]) -> list[Project]:
        # "processed" = projects we're done with this tick, either notified or
        # deliberately skipped by the primary check. Both get marked seen and
        # advance the watermark so they're never reconsidered. A send *failure*
        # (vs. a skip) breaks the loop so the project is retried on the next tick.
        processed: list[Project] = []
        # Passed the primary check AND notified — feeds the filtered (📂) view.
        notified: list[Project] = []
        for project in projects:
            if not await self._store.is_active():
                break
            project = await self._current_delivery_project(project)
            if project is None:
                continue
            if await self._store.is_seen(project.id):
                processed.append(project)
                continue
            allowed = await self._passes_primary_check(project)
            if not await self._store.is_active():
                break
            project = await self._current_delivery_project(project)
            if project is None:
                continue
            if not allowed:
                processed.append(project)
                continue
            if not await self._send_project(project):
                break
            processed.append(project)
            notified.append(project)
            await asyncio.sleep(1)
        if processed:
            await self._store.mark_seen([p.id for p in processed])
        if notified:
            await self._store.mark_passed([p.id for p in notified])
        return processed

    async def _passes_primary_check(self, project: Project) -> bool:
        """Primary check, run before any notification or bid generation: ask the
        AI whether the order's stack is one we work with. Independent of the
        secondary pass (bid generation). No screener configured (AI off) means
        everything passes, preserving the original behaviour."""
        if self._screener is None:
            return True
        result = await self._screener.screen(project)
        if not result.allowed:
            log.info(
                "primary check skipped project %s (stack=%s, reason=%s): %s",
                project.id, result.stack or "?", getattr(result, "reason", "") or "?", project.title,
            )
        elif getattr(result, "tier", "") == "maybe":
            self._screen_notes[project.id] = getattr(result, "reason", "")
        return result.allowed

    async def _send_project(self, project: Project) -> bool:
        text = formatting.format_project_notification(
            project, maybe_note=self._screen_notes.pop(project.id, None))
        try:
            await self._bot.send_message(
                chat_id=self._settings.telegram_chat_id,
                text=text,
                reply_markup=keyboards.notification_keyboard(project.id, ai_enabled=self._ai_allowed),
            )
        except TelegramForbiddenError:
            await self._store.set_active(False)
            log.info("disabled notifications for blocked chat %s", self._settings.telegram_chat_id)
            return False
        except TelegramAPIError:
            log.exception("failed to send project %s", project.id)
            return False
        log.info("sent project %s: %s", project.id, project.title)

        return True
