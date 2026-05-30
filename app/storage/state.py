import asyncio
import json
import logging
from pathlib import Path

import aiofiles

from app.projects import Project

log = logging.getLogger(__name__)


class StateStore:
    """Async, lock-protected persisted state.

    Holds the set of project IDs already announced, a rolling history of the
    most recent projects (used by the /start menu), and a per-skill publish
    watermark. A category is considered "first seen" until it has a watermark
    entry — see ``has_watermark``.
    """

    def __init__(self, path: Path, history_size: int = 50, seen_size: int = 500) -> None:
        self._path = path
        self._history_size = history_size
        self._seen_size = seen_size
        self._lock = asyncio.Lock()
        self._seen_ids: list[str] = []
        # IDs of projects that passed the primary check and were notified. Kept
        # separately from Project (which add_projects overwrites on re-fetch) so
        # the verdict survives. Powers the filtered (📂) view in the /start menu.
        self._passed_ids: list[str] = []
        self._projects: list[Project] = []
        # Watermark per skill_id: the publish timestamp of the last announced
        # project in that category. Absence of an entry means "not yet seen".
        self._watermarks: dict[str, int] = {}
        self._skill_ids: list[int] | None = None
        self._muted_skill_ids: list[int] = []
        self._load_sync()

    def _load_sync(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("failed to read state from %s, starting fresh", self._path)
            return
        self._seen_ids = [str(x) for x in data.get("seen_ids", [])]
        self._passed_ids = [str(x) for x in data.get("passed_ids", [])]
        self._projects = [Project.from_dict(p) for p in data.get("projects", [])]
        raw_watermark = data.get("last_published_ts", {})
        if isinstance(raw_watermark, dict):
            self._watermarks = {str(k): int(v) for k, v in raw_watermark.items()}
        # Older single-int formats are intentionally dropped: every category is
        # then treated as first-seen and its current backlog is suppressed
        # (guarded further by seen_ids) instead of being re-announced.
        raw_skill_ids = data.get("skill_ids")
        if isinstance(raw_skill_ids, list):
            skill_ids = _dedupe_ints(raw_skill_ids)
            if skill_ids:
                self._skill_ids = skill_ids
        self._muted_skill_ids = _dedupe_ints(data.get("muted_skill_ids", []))

    async def _persist_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seen_ids": self._seen_ids,
            "passed_ids": self._passed_ids,
            "projects": [p.to_dict() for p in self._projects],
            "last_published_ts": self._watermarks,
            "skill_ids": self._skill_ids,
            "muted_skill_ids": self._muted_skill_ids,
        }
        tmp = self._path.with_suffix(".tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False))
        tmp.replace(self._path)

    def has_watermark(self, skill_id: int) -> bool:
        return str(skill_id) in self._watermarks

    def last_published_ts(self, skill_id: int) -> int:
        return self._watermarks.get(str(skill_id), 0)

    async def is_seen(self, project_id: str) -> bool:
        async with self._lock:
            return project_id in self._seen_ids

    async def mark_seen(self, project_ids: list[str]) -> None:
        if not project_ids:
            return
        async with self._lock:
            existing = set(self._seen_ids)
            for project_id in project_ids:
                if project_id not in existing:
                    self._seen_ids.append(project_id)
                    existing.add(project_id)
            # Keep the most recently seen IDs (insertion order = recency).
            self._seen_ids = self._seen_ids[-self._seen_size:]
            await self._persist_locked()

    async def mark_passed(self, project_ids: list[str]) -> None:
        """Record projects that passed the primary check and were notified, so
        the filtered (📂) /start view can show only them."""
        if not project_ids:
            return
        async with self._lock:
            existing = set(self._passed_ids)
            for project_id in project_ids:
                if project_id not in existing:
                    self._passed_ids.append(project_id)
                    existing.add(project_id)
            self._passed_ids = self._passed_ids[-self._seen_size:]
            await self._persist_locked()

    async def set_passed(self, project_ids: list[str]) -> None:
        """Replace the whole passed set (used by the startup re-filter, which
        recomputes it from scratch each launch)."""
        async with self._lock:
            seen: set[str] = set()
            ordered: list[str] = []
            for project_id in project_ids:
                if project_id not in seen:
                    seen.add(project_id)
                    ordered.append(project_id)
            self._passed_ids = ordered[-self._seen_size:]
            await self._persist_locked()

    async def passed_ids(self) -> set[str]:
        async with self._lock:
            return set(self._passed_ids)

    async def skill_ids(self, fallback: list[int]) -> list[int]:
        async with self._lock:
            return list(self._skill_ids if self._skill_ids is not None else fallback)

    async def add_skill_id(self, skill_id: int, fallback: list[int]) -> bool:
        """Persist a watched skill id. Returns False when it already exists."""
        async with self._lock:
            current = list(self._skill_ids if self._skill_ids is not None else fallback)
            if skill_id in current:
                return False
            current.append(skill_id)
            self._skill_ids = current
            await self._persist_locked()
            return True

    async def muted_skill_ids(self) -> set[int]:
        async with self._lock:
            return set(self._muted_skill_ids)

    async def toggle_muted_skill_id(self, skill_id: int) -> bool:
        """Toggle category notifications. Returns True when muted after toggle."""
        async with self._lock:
            if skill_id in self._muted_skill_ids:
                self._muted_skill_ids = [x for x in self._muted_skill_ids if x != skill_id]
                muted = False
            else:
                self._muted_skill_ids.append(skill_id)
                muted = True
            await self._persist_locked()
            return muted

    async def add_projects(self, projects: list[Project]) -> None:
        if not projects:
            return
        async with self._lock:
            merged: dict[str, Project] = {p.id: p for p in self._projects}
            for p in projects:
                merged[p.id] = p
            # Keep the most recent history_size projects PER category, so a busy
            # category can't evict another's history out of the /start menu.
            per_skill: dict[int, list[Project]] = {}
            for p in merged.values():
                per_skill.setdefault(p.skill_id, []).append(p)
            kept: list[Project] = []
            for group in per_skill.values():
                group.sort(key=lambda p: p.published_ts, reverse=True)
                kept.extend(group[: self._history_size])
            self._projects = sorted(kept, key=lambda p: p.published_ts, reverse=True)
            await self._persist_locked()

    async def recent_projects(self) -> list[Project]:
        async with self._lock:
            return list(self._projects)

    async def find_project(self, project_id: str) -> Project | None:
        async with self._lock:
            for p in self._projects:
                if p.id == project_id:
                    return p
            return None

    async def update_last_published_ts(self, skill_id: int, ts: int) -> None:
        key = str(skill_id)
        async with self._lock:
            # Seed the entry on first sight even if ts == 0, so the category is
            # no longer treated as first-seen on the next tick.
            if key not in self._watermarks or ts > self._watermarks[key]:
                self._watermarks[key] = ts
                await self._persist_locked()


def _dedupe_ints(values: object) -> list[int]:
    if not isinstance(values, list):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in seen:
            seen.add(item)
            result.append(item)
    return result
