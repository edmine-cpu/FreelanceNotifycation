"""Checks whether orders we generated bids for were won, via the official API."""
import asyncio
import logging

from aiogram import Bot

from app.ai.store import AIStore
from app.source import FreelancehuntSource

log = logging.getLogger(__name__)

STATUS_OPEN = 11  # "Open for proposals"; other statuses without a winner are closed.


def outcome_for(status_id: int, winner: str | None, my_login: str) -> str:
    if winner:
        return "won" if winner.lower() == my_login.lower() else "lost"
    return "open" if status_id == STATUS_OPEN else "closed"


class OutcomeTracker:
    def __init__(self, bot: Bot, source: FreelancehuntSource, store: AIStore, user_id: int,
                 interval: float = 1800) -> None:
        self._bot, self._source, self._store = bot, source, store
        self._user_id, self._interval = user_id, interval
        self._login: str | None = None

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.check_once()
            except Exception:
                log.exception("bid outcome check failed")
            try:
                await asyncio.wait_for(stop_event.wait(), self._interval)
            except asyncio.TimeoutError:
                pass

    async def check_once(self) -> None:
        rows = self._store.bids_to_check(self._user_id)
        if not rows:
            return
        if self._login is None:
            self._login = await self._source.my_login()
        for row in rows:
            try:
                status_id, winner = await self._source.project_outcome(row["project_id"])
            except Exception:
                log.warning("could not check outcome of project %s", row["project_id"])
                continue
            outcome = outcome_for(status_id, winner, self._login)
            self._store.set_bid_outcome(self._user_id, row["project_id"], outcome, winner)
            if outcome in ("won", "lost") and row["sent"]:
                await self._notify(row, outcome)

    async def _notify(self, row: dict, outcome: str) -> None:
        price = f"{row['amount']} {row['currency']}" if row["amount"] else "без цены"
        budget = row["budget"] or "не указан"
        head = "🏆 Тебя выбрали исполнителем" if outcome == "won" else "Заказчик выбрал другого исполнителя"
        text = f"{head}: {row['title']}\nТвоя цена: {price}, бюджет: {budget}\n{row['url']}"
        try:
            await self._bot.send_message(self._user_id, text, parse_mode=None)
        except Exception:
            log.warning("could not send bid outcome for project %s", row["project_id"])
