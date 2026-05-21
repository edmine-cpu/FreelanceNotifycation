import logging

from app.config import Settings
from app.storage import StateStore
from app.telegram import keyboards
from app.telegram.client import TelegramClient, TelegramError

from .views import projects_page_view, start_view

log = logging.getLogger(__name__)


class CallbackHandler:
    def __init__(self, client: TelegramClient, store: StateStore, settings: Settings) -> None:
        self._client = client
        self._store = store
        self._settings = settings

    def handle(self, callback: dict) -> None:
        data = callback.get("data", "") or ""
        message = callback.get("message")
        callback_id = callback["id"]

        if message is None:
            self._client.answer_callback_query(callback_id)
            return

        chat_id = message["chat"]["id"]
        message_id = message["message_id"]

        if data == keyboards.CALLBACK_NOOP:
            self._client.answer_callback_query(callback_id)
            return

        if data == keyboards.CALLBACK_START:
            text, markup = start_view(self._settings)
            self._safe_edit(chat_id, message_id, text, markup)
            self._client.answer_callback_query(callback_id)
            return

        if data.startswith(keyboards.CALLBACK_LIST_PREFIX):
            page = _parse_page(data)
            projects = self._store.recent_projects()
            text, markup = projects_page_view(projects, page, self._settings)
            self._safe_edit(chat_id, message_id, text, markup)
            self._client.answer_callback_query(callback_id)
            return

        log.warning("unknown callback data: %r", data)
        self._client.answer_callback_query(callback_id)

    def _safe_edit(self, chat_id: int, message_id: int, text: str, markup: dict) -> None:
        try:
            self._client.edit_message_text(chat_id, message_id, text, reply_markup=markup)
        except TelegramError as exc:
            # Telegram returns an error if the new content is identical — that's fine.
            if "message is not modified" in str(exc):
                return
            log.exception("editMessageText failed")


def _parse_page(data: str) -> int:
    raw = data[len(keyboards.CALLBACK_LIST_PREFIX):]
    try:
        return max(0, int(raw))
    except ValueError:
        return 0
