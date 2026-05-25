import asyncio
import logging
import signal

from app.config import Settings
from app.gemini import BidGenerator, GeminiClient
from app.notifier import NotifierLoop
from app.rates import RatesProvider
from app.source import FreelancehuntSource
from app.storage import StateStore
from app.telegram import build_bot, build_dispatcher

log = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    categories = settings.categories
    log.info(
        "watching %d categories: %s",
        len(categories),
        ", ".join(f"{c.name} (skill {c.skill_id})" for c in categories),
    )
    bot = build_bot(settings.telegram_bot_token.get_secret_value())
    store = StateStore(settings.state_file, history_size=settings.history_size)
    source = FreelancehuntSource(
        token=settings.freelancehunt_token.get_secret_value(),
        categories=categories,
    )

    bid_generator: BidGenerator | None = None
    if settings.gemini_active:
        gemini = GeminiClient(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            timeout=settings.gemini_timeout_sec,
        )
        rates_provider = RatesProvider(fallback=settings.fallback_rates)
        bid_generator = BidGenerator(gemini, rates_provider=rates_provider)
        log.info("gemini enabled, model=%s", settings.gemini_model)
    else:
        log.info("gemini disabled (no API key or GEMINI_ENABLED=false)")

    dispatcher = build_dispatcher(settings, store, bid_generator)
    notifier = NotifierLoop(bot, store, source, settings, bid_generator)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    polling_task = asyncio.create_task(
        dispatcher.start_polling(bot, handle_signals=False),
        name="updates-polling",
    )
    notifier_task = asyncio.create_task(notifier.run(stop_event), name="notifier")

    await stop_event.wait()
    log.info("shutdown requested")
    await dispatcher.stop_polling()

    for task in (polling_task, notifier_task):
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:
            log.exception("task %s exited with error", task.get_name())

    await source.aclose()
    await bot.session.close()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows fallback — not actually used here, but keeps tests happy.
            signal.signal(sig, lambda *_: stop_event.set())
