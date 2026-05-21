import logging
import threading
from typing import Callable

from .client import TelegramClient, TelegramError

log = logging.getLogger(__name__)


class UpdatesPoller:
    def __init__(
        self,
        client: TelegramClient,
        handler: Callable[[dict], None],
        long_poll_timeout: int = 25,
    ) -> None:
        self._client = client
        self._handler = handler
        self._timeout = long_poll_timeout
        self._offset: int | None = None

    def run(self, stop_event: threading.Event) -> None:
        log.info("starting telegram updates poller")
        while not stop_event.is_set():
            try:
                updates = self._client.get_updates(self._offset, self._timeout)
            except TelegramError:
                log.exception("getUpdates failed")
                stop_event.wait(5)
                continue
            except Exception:
                log.exception("unexpected error in updates loop")
                stop_event.wait(5)
                continue

            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    self._handler(update)
                except Exception:
                    log.exception("update handler failed for update %s", update.get("update_id"))
        log.info("updates poller stopped")
