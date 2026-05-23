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
        self._projects: list[Project] = []
        # Watermark per skill_id: the publish timestamp of the last announced
        # project in that category. Absence of an entry means "not yet seen".
        self._watermarks: dict[str, int] = {}
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
        self._projects = [Project.from_dict(p) for p in data.get("projects", [])]
        raw_watermark = data.get("last_published_ts", {})
        if isinstance(raw_watermark, dict):
            self._watermarks = {str(k): int(v) for k, v in raw_watermark.items()}
        # Older single-int formats are intentionally dropped: every category is
        # then treated as first-seen and its current backlog is suppressed
        # (guarded further by seen_ids) instead of being re-announced.

    async def _persist_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seen_ids": self._seen_ids,
            "projects": [p.to_dict() for p in self._projects],
            "last_published_ts": self._watermarks,
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
