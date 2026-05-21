import logging
import signal
import threading

from app.config import Settings
from app.handlers import UpdateRouter
from app.handlers.callbacks import CallbackHandler
from app.handlers.commands import CommandHandler
from app.notifier import NotifierLoop
from app.parser import FreelancehuntParser
from app.storage import StateStore
from app.telegram import TelegramClient, UpdatesPoller

log = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    client = TelegramClient(settings.telegram_token)
    store = StateStore(settings.state_file, history_size=settings.history_size)
    parser = FreelancehuntParser(settings.listing_url, settings.user_agent)

    router = UpdateRouter(
        commands=CommandHandler(client, settings),
        callbacks=CallbackHandler(client, store, settings),
    )
    poller = UpdatesPoller(client, router)
    notifier = NotifierLoop(client, store, parser, settings)

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    threads = [
        threading.Thread(target=notifier.run, args=(stop_event,), name="notifier", daemon=True),
        threading.Thread(target=poller.run, args=(stop_event,), name="updates", daemon=True),
    ]
    for t in threads:
        t.start()

    stop_event.wait()
    log.info("shutdown requested, waiting for threads")
    for t in threads:
        t.join(timeout=5)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handler(signum, _frame):
        log.info("received signal %s", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
