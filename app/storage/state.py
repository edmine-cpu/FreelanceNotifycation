import asyncio
import json
import logging
from pathlib import Path

import aiofiles

from app.projects import Project

log = logging.getLogger(__name__)


class StateStore:
    """Async, lock-protected persisted state.

    Holds the set of project IDs already announced and a rolling history of
    the most recent projects (used by the /start menu).
    """

    def __init__(self, path: Path, history_size: int = 50, seen_size: int = 500) -> None:
        self._path = path
        self._history_size = history_size
        self._seen_size = seen_size
        self._lock = asyncio.Lock()
        self._seen_ids: list[str] = []
        self._projects: list[Project] = []
        self._initialized: bool = False
        self._last_published_ts: int = 0
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
        self._initialized = bool(data.get("initialized", False))
        self._last_published_ts = int(data.get("last_published_ts", 0))

    async def _persist_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "initialized": self._initialized,
            "seen_ids": self._seen_ids,
            "projects": [p.to_dict() for p in self._projects],
            "last_published_ts": self._last_published_ts,
        }
        tmp = self._path.with_suffix(".tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(json.dumps(payload, ensure_ascii=False))
        tmp.replace(self._path)

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def last_published_ts(self) -> int:
        return self._last_published_ts

    async def is_seen(self, project_id: str) -> bool:
        async with self._lock:
            return project_id in self._seen_ids

    async def mark_seen(self, project_ids: list[str]) -> None:
        if not project_ids:
            return
        async with self._lock:
            seen = set(self._seen_ids)
            seen.update(project_ids)
            self._seen_ids = sorted(seen)[-self._seen_size:]
            await self._persist_locked()

    async def mark_initialized(self) -> None:
        async with self._lock:
            self._initialized = True
            await self._persist_locked()

    async def add_projects(self, projects: list[Project]) -> None:
        if not projects:
            return
        async with self._lock:
            merged: dict[str, Project] = {p.id: p for p in self._projects}
            for p in projects:
                merged[p.id] = p
            ordered = sorted(merged.values(), key=lambda p: p.published_ts, reverse=True)
            self._projects = ordered[: self._history_size]
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

    async def update_last_published_ts(self, ts: int) -> None:
        async with self._lock:
            if ts > self._last_published_ts:
                self._last_published_ts = ts
                await self._persist_locked()
