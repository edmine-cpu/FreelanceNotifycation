"""Small transactional AI store. No prompts/corrections are written to logs."""
import asyncio
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.llm import AIRequest
from .policy import require_ai_access

PRICE_VERSION = "anthropic-standard-global-2026-09-28"
# USD / million: uncached input, output (includes thinking), 5m, 1h, read.
PRICES = {
    "claude-haiku-4-5-20251001": ("1", "5", "1.25", "2", "0.10"),
    "claude-opus-5-5": ("4", "20", "5", "8", "0.20"),
}


def fingerprint(*values) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def usage_cost(model: str, usage: dict) -> tuple[dict, Decimal | None]:
    def number(name, source=usage):
        value = source.get(name, 0)
        if type(value) is not int or value < 0:
            raise ValueError("invalid usage")
        return value
    tokens = {"input": number("input_tokens"), "output": number("output_tokens"),
              "read": number("cache_read_input_tokens")}
    created = number("cache_creation_input_tokens")
    details = usage.get("cache_creation") or {}
    tokens["write1h"] = number("ephemeral_1h_input_tokens", details)
    tokens["write5m"] = number("ephemeral_5m_input_tokens", details) if details else created
    if tokens["write5m"] + tokens["write1h"] != created:
        raise ValueError("inconsistent cache usage")
    if model not in PRICES or "input_tokens" not in usage or "output_tokens" not in usage:
        return tokens, None
    prices = map(Decimal, PRICES[model])
    counts = (tokens[k] for k in ("input", "output", "write5m", "write1h", "read"))
    return tokens, sum((p * n for p, n in zip(prices, counts)), Decimal(0)) / 1_000_000


class AIStore:
    def __init__(self, path: Path | str = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS results (
            key TEXT PRIMARY KEY, user_id INTEGER NOT NULL, payload TEXT NOT NULL,
            retry_at REAL NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, key TEXT NOT NULL,
            status TEXT NOT NULL, payload TEXT, created REAL NOT NULL);
          CREATE UNIQUE INDEX IF NOT EXISTS active_key ON operations(key) WHERE status='running';
          CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL, user_id INTEGER NOT NULL,
            project_id TEXT NOT NULL, purpose TEXT NOT NULL, model TEXT NOT NULL,
            created REAL NOT NULL, status TEXT NOT NULL, request_id TEXT,
            tokens TEXT, cost TEXT, price_version TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS alerts (
            month TEXT NOT NULL, threshold TEXT NOT NULL, level INTEGER NOT NULL,
            PRIMARY KEY(month, threshold, level));
          CREATE TABLE IF NOT EXISTS correction_events (user_id INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY(user_id,event));
          CREATE TABLE IF NOT EXISTS revisions (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, origin TEXT NOT NULL,
            project_id TEXT NOT NULL, previous TEXT NOT NULL, target INTEGER NOT NULL,
            expires REAL NOT NULL, status TEXT NOT NULL, corrections TEXT,
            result TEXT, UNIQUE(user_id, origin));
        """)
        self.flights: dict[str, asyncio.Task] = {}

    def close(self):
        self.db.close()

    def recover_interrupted(self):
        """Call once at application startup, before starting any AI tasks."""
        with self.db:
            rows = self.db.execute("SELECT id FROM revisions WHERE status='running'").fetchall()
            for row in rows:
                operation = self.db.execute("SELECT status,payload FROM operations WHERE id=?", (row[0],)).fetchone()
                result = operation["payload"] if operation and operation["status"] == "success" else None
                self.db.execute("UPDATE revisions SET status=?,result=? WHERE id=?",
                                ("delivery_failed" if result else "failed", result, row[0]))
            self.db.execute("UPDATE operations SET status='uncertain' WHERE status='running'")

    def cached(self, user_id: int, key: str):
        require_ai_access(user_id)
        row = self.db.execute("SELECT * FROM results WHERE key=? AND user_id=?", (key, user_id)).fetchone()
        return (json.loads(row["payload"]), row["retry_at"]) if row else None

    def save(self, user_id: int, key: str, payload: dict, retry_at: float = 0):
        require_ai_access(user_id)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?)",
                            (key, user_id, json.dumps(payload, ensure_ascii=False), retry_at))

    def operation(self, user_id: int, operation_id: str):
        row = self.db.execute("SELECT * FROM operations WHERE id=? AND user_id=?", (operation_id, user_id)).fetchone()
        return dict(row) if row else None

    def active_operation(self, user_id, key):
        row = self.db.execute("SELECT id FROM operations WHERE user_id=? AND key=? AND status='running' AND created>=?",
                              (user_id, key, time.time()-600)).fetchone()
        return row[0] if row else None

    async def wait_operation(self, user_id, operation_id):
        for _ in range(6000):
            row = self.operation(user_id, operation_id)
            if not row or row["status"] != "running" or row["created"] < time.time()-600:
                if row and row["status"] == "success" and row["payload"]:
                    return json.loads(row["payload"])
                raise ValueError("Предыдущая операция не завершилась успешно. Нажмите кнопку снова для явной попытки.")
            await asyncio.sleep(0.1)
        raise ValueError("Результат операции пока неизвестен. Попробуйте открыть ставку позже.")

    def claim(self, user_id: int, operation_id: str, key: str):
        require_ai_access(user_id)
        with self.db:
            # An interrupted request keeps its ledger row with unknown cost. A
            # new explicit action may retry after the maximum request lifetime.
            self.db.execute("UPDATE operations SET status='uncertain' WHERE status='running' AND created<?", (time.time()-600,))
            try:
                self.db.execute("INSERT INTO operations VALUES (?,?,?,'running',NULL,?)",
                                (operation_id, user_id, key, time.time()))
            except sqlite3.IntegrityError:
                raise ValueError("Операция уже выполнялась. Откройте ставку снова; для новой попытки нажмите кнопку ещё раз.") from None

    def finish(self, operation_id: str, status: str, payload=None):
        with self.db:
            self.db.execute("UPDATE operations SET status=?,payload=? WHERE id=?",
                            (status, json.dumps(payload, ensure_ascii=False) if payload else None, operation_id))

    async def singleflight(self, key, work):
        task = self.flights.get(key)
        if task is None:
            task = asyncio.create_task(work())
            self.flights[key] = task
            def done(completed):
                if self.flights.get(key) is completed:
                    self.flights.pop(key, None)
                if not completed.cancelled():
                    completed.exception()  # retrieve errors if the Telegram waiter was cancelled
            task.add_done_callback(done)
        return await asyncio.shield(task)

    def start_attempt(self, context: AIRequest, model: str) -> int:
        require_ai_access(context.user_id)
        with self.db:
            cursor = self.db.execute("""INSERT INTO usage
              (operation_id,user_id,project_id,purpose,model,created,status,price_version)
              VALUES (?,?,?,?,?,?,'uncertain',?)""",
              (context.operation_id, context.user_id, context.project_id, context.purpose,
               model, time.time(), PRICE_VERSION))
            return cursor.lastrowid

    def record(self, attempt: int, *, status: str, model: str, request_id="", usage=None, rejected=False):
        tokens, cost = {}, Decimal(0) if rejected else None
        if usage is not None:
            try:
                tokens, cost = usage_cost(model, usage)
            except (ValueError, TypeError, AttributeError):
                status = "invalid_usage"
        with self.db:
            self.db.execute("UPDATE usage SET status=?,model=?,request_id=?,tokens=?,cost=? WHERE id=?",
                            (status, model, request_id, json.dumps(tokens), str(cost) if cost is not None else None, attempt))

    def validation_failed(self, operation_id):
        with self.db:
            self.db.execute("UPDATE usage SET status='invalid_result' WHERE operation_id=? AND status='success'", (operation_id,))

    def cache_hit(self, context: AIRequest, model: str):
        attempt = self.start_attempt(context, model)
        self.record(attempt, status="local_cache", model=model, rejected=True)

    def report(self, user_id: int, tz: str, now: datetime | None = None) -> str:
        require_ai_access(user_id)
        zone = ZoneInfo(tz)
        now = (now or datetime.now(timezone.utc)).astimezone(zone)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.db.execute("SELECT * FROM usage WHERE user_id=? AND created<=?", (user_id, now.timestamp())).fetchall()
        lines = [f"Расходы AI · {tz}"]
        for name, start in (("Сегодня", day), ("Последние 7 дней", day-timedelta(days=6)),
                            ("Текущий месяц", day.replace(day=1))):
            period = [r for r in rows if r["created"] >= start.timestamp()]
            total = sum((Decimal(r["cost"]) for r in period if r["cost"] is not None), Decimal(0))
            unknown = sum(r["cost"] is None for r in period)
            hits = sum(r["status"] == "local_cache" for r in period)
            lines.append(f"\n{name}: ${total:.6f}; кеш: {hits}; неопределённых попыток: {unknown}")
            groups = sorted({(r["model"], r["purpose"]) for r in period if r["status"] != "local_cache"})
            for model, purpose in groups:
                group = [r for r in period if r["model"] == model and r["purpose"] == purpose and r["status"] != "local_cache"]
                value = sum((Decimal(r["cost"]) for r in group if r["cost"] is not None), Decimal(0))
                count = len({r["operation_id"] for r in group})
                lines.append(f"{model} · {purpose}: {count} операций, ${value:.6f}")
        lines.append("\nУчтённый расход этого бота, без налогов и сторонних операций. Денежный порог не останавливает AI.")
        return "\n".join(lines)

    def claim_alerts(self, user_id: int, threshold: Decimal, tz: str, now: datetime | None = None):
        require_ai_access(user_id)
        if threshold <= 0:
            return []
        now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz))
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month = now.strftime("%Y-%m")
        alerts = []
        with self.db:
            rows = self.db.execute("SELECT cost FROM usage WHERE user_id=? AND created>=? AND created<=? AND cost IS NOT NULL",
                                   (user_id, start.timestamp(), now.timestamp())).fetchall()
            total = sum((Decimal(r[0]) for r in rows), Decimal(0))
            for level in (80, 100):
                if total >= threshold * Decimal(level) / 100:
                    cursor = self.db.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?)", (month, str(threshold), level))
                    if cursor.rowcount:
                        alerts.append((month, str(threshold), level, total))
        return alerts

    def release_alert(self, month, threshold, level):
        with self.db:
            self.db.execute("DELETE FROM alerts WHERE month=? AND threshold=? AND level=?", (month, threshold, level))

    def revision(self, user_id, operation_id):
        require_ai_access(user_id)
        row = self.db.execute("SELECT * FROM revisions WHERE id=? AND user_id=?", (operation_id, user_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["previous"] = json.loads(result["previous"])
        return result

    def begin_revision(self, user_id, origin, project_id, previous, target, ttl=900):
        require_ai_access(user_id)
        with self.db:
            old = self.db.execute("SELECT id FROM revisions WHERE user_id=? AND origin=?", (user_id, origin)).fetchone()
            if old:
                return self.revision(user_id, old[0])
            self.db.execute("UPDATE revisions SET status='cancelled' WHERE user_id=? AND status='waiting'", (user_id,))
            operation_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,'waiting',NULL,NULL)",
                            (operation_id, user_id, origin, project_id, json.dumps(previous, ensure_ascii=False), target, time.time()+ttl))
        return self.revision(user_id, operation_id)

    def cancel_waiting(self, user_id):
        with self.db:
            self.db.execute("UPDATE revisions SET status='cancelled' WHERE user_id=? AND status='waiting'", (user_id,))

    def consume_revision(self, user_id, operation_id, corrections: str, *, event: str | None = None):
        require_ai_access(user_id)
        with self.db:
            if event is not None:
                cursor = self.db.execute("INSERT OR IGNORE INTO correction_events VALUES (?,?)", (user_id, event))
                if not cursor.rowcount:
                    return None
            self.db.execute("UPDATE revisions SET status='expired' WHERE user_id=? AND status='waiting' AND expires<=?", (user_id, time.time()))
            cursor = self.db.execute("UPDATE revisions SET status='running', corrections=? WHERE id=? AND user_id=? AND status='waiting'",
                                     (corrections, operation_id, user_id))
        return self.revision(user_id, operation_id) if cursor.rowcount else None

    def finish_revision(self, operation_id, status, result=None):
        with self.db:
            self.db.execute("UPDATE revisions SET status=?,result=? WHERE id=?", (status, result, operation_id))

    def bid(self, user_id, project_id, version):
        require_ai_access(user_id)
        row = self.db.execute("SELECT payload FROM operations WHERE user_id=? AND status='success' AND json_extract(payload,'$.version')=? AND json_extract(payload,'$.project_id')=? LIMIT 1",
                              (user_id, version, project_id)).fetchone()
        return json.loads(row[0]) if row else None

    def waiting_revision(self, user_id):
        require_ai_access(user_id)
        row = self.db.execute("SELECT id FROM revisions WHERE user_id=? AND status='waiting' ORDER BY expires DESC LIMIT 1", (user_id,)).fetchone()
        return self.revision(user_id, row[0]) if row else None

    def cancel_revision(self, user_id, operation_id):
        require_ai_access(user_id)
        with self.db:
            self.db.execute("UPDATE revisions SET status='cancelled' WHERE id=? AND user_id=? AND status='waiting'", (operation_id, user_id))
