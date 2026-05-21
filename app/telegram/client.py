import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, timeout: int = 30) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._timeout = timeout
        self._session = requests.Session()

    def call(self, method: str, payload: dict | None = None, timeout: int | None = None) -> dict:
        url = f"{self._base}/{method}"
        resp = self._session.post(url, json=payload or {}, timeout=timeout or self._timeout)
        try:
            data = resp.json()
        except ValueError as exc:
            raise TelegramError(f"non-JSON response from {method}: {resp.text!r}") from exc
        if not resp.ok or not data.get("ok"):
            raise TelegramError(f"{method} failed [{resp.status_code}]: {data}")
        return data["result"]

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)

    def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self.call("editMessageText", payload)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        try:
            self.call("answerCallbackQuery", payload)
        except TelegramError:
            log.exception("answerCallbackQuery failed")

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout + 10)
