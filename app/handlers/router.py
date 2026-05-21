import logging

from .callbacks import CallbackHandler
from .commands import CommandHandler

log = logging.getLogger(__name__)


class UpdateRouter:
    def __init__(self, commands: CommandHandler, callbacks: CallbackHandler) -> None:
        self._commands = commands
        self._callbacks = callbacks

    def __call__(self, update: dict) -> None:
        if "message" in update and update["message"].get("text"):
            self._commands.handle(update["message"])
            return
        if "callback_query" in update:
            self._callbacks.handle(update["callback_query"])
            return
        log.debug("ignored update: %s", update.keys())
