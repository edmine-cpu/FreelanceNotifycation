import logging

from app.config import Settings
from app.telegram.client import TelegramClient

from .views import start_view

log = logging.getLogger(__name__)


class CommandHandler:
    def __init__(self, client: TelegramClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def handle(self, message: dict) -> None:
        text = (message.get("text") or "").strip()
        chat_id = message["chat"]["id"]
        if text.startswith("/start"):
            self._handle_start(chat_id)
        elif text.startswith("/help"):
            self._handle_start(chat_id)

    def _handle_start(self, chat_id: int) -> None:
        body, markup = start_view(self._settings)
        self._client.send_message(chat_id, body, reply_markup=markup, disable_web_page_preview=True)
        log.info("sent /start menu to chat %s", chat_id)
