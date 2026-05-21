import json
import logging
import threading
from pathlib import Path

from app.projects import Project

log = logging.getLogger(__name__)


class StateStore:
    """Thread-safe persisted state.

    Holds the set of project IDs already announced and a rolling history of
    the most recent projects (used by the /start menu).
    """

    def __init__(self, path: Path, history_size: int = 50, seen_size: int = 500) -> None:
        self._path = path
        self._history_size = history_size
        self._seen_size = seen_size
        self._lock = threading.Lock()
        self._seen_ids: list[str] = []
        self._projects: list[Project] = []
        self._initialized: bool = False
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            log.exception("failed to read state from %s, starting fresh", self._path)
            return
        self._seen_ids = [str(x) for x in data.get("seen_ids", [])]
        self._projects = [Project.from_dict(p) for p in data.get("projects", [])]
        self._initialized = bool(data.get("initialized", False))

    def _persist_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "initialized": self._initialized,
            "seen_ids": self._seen_ids,
            "projects": [p.to_dict() for p in self._projects],
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(self._path)

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._initialized

    def is_seen(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._seen_ids

    def mark_seen(self, project_ids: list[str]) -> None:
        if not project_ids:
            return
        with self._lock:
            seen = set(self._seen_ids)
            seen.update(project_ids)
            self._seen_ids = sorted(seen)[-self._seen_size:]
            self._persist_locked()

    def mark_initialized(self) -> None:
        with self._lock:
            self._initialized = True
            self._persist_locked()

    def add_projects(self, projects: list[Project]) -> None:
        """Merge projects into history, dedupe by id, keep newest first."""
        if not projects:
            return
        with self._lock:
            merged: dict[str, Project] = {p.id: p for p in self._projects}
            for p in projects:
                merged[p.id] = p
            ordered = sorted(merged.values(), key=lambda p: p.published_ts, reverse=True)
            self._projects = ordered[: self._history_size]
            self._persist_locked()

    def recent_projects(self) -> list[Project]:
        with self._lock:
            return list(self._projects)
