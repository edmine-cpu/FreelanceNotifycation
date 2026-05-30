import asyncio
import logging
import signal

from app.config import Settings
from app.ai import BidGenerator, GroqClient, OrderScreener
from app.ai.prompt_store import ensure_prompt_json
from app.notifier import NotifierLoop
from app.rates import RatesProvider
from app.source import FreelancehuntSource
from app.storage import StateStore
from app.telegram import build_bot, build_dispatcher

log = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    store = StateStore(settings.state_file, history_size=settings.history_size)
    skill_ids = await store.skill_ids([category.skill_id for category in settings.categories])
    settings.skill_ids = ",".join(str(skill_id) for skill_id in skill_ids)
    prompt_examples_path = settings.prompt_examples_file or settings.state_file.with_name(
        "bids_examples.json"
    )
    ensure_prompt_json(prompt_examples_path)
    categories = settings.categories
    log.info(
        "watching %d categories: %s",
        len(categories),
        ", ".join(f"{c.name} (skill {c.skill_id})" for c in categories),
    )
    bot = build_bot(settings.telegram_bot_token.get_secret_value())
    source = FreelancehuntSource(
        token=settings.freelancehunt_token.get_secret_value(),
        categories=categories,
    )

    bid_generator: BidGenerator | None = None
    screener: OrderScreener | None = None
    if settings.ai_active:
        groq = GroqClient(
            api_key=settings.groq_api_key.get_secret_value(),
            model=settings.groq_model,
            timeout=settings.groq_timeout_sec,
        )
        rates_provider = RatesProvider(fallback=settings.fallback_rates)
        bid_generator = BidGenerator(
            groq,
            examples_path=prompt_examples_path,
            rates_provider=rates_provider,
        )
        # Primary check is fail-open and runs on every new project inside a tick,
        # so it must not block: give it its own client with minimal retries
        # instead of the bid generator's full backoff. The secondary pass keeps
        # full retries — it's user-triggered and wants to succeed.
        screen_client = GroqClient(
            api_key=settings.groq_api_key.get_secret_value(),
            model=settings.groq_model,
            timeout=settings.groq_timeout_sec,
            max_retries=1,
        )
        screener = OrderScreener(screen_client)
        log.info("ai enabled, model=%s", settings.groq_model)
    else:
        log.info("ai disabled (no API key or GROQ_ENABLED=false)")

    dispatcher = build_dispatcher(
        settings,
        store,
        bid_generator,
        source,
        prompt_examples_path=prompt_examples_path,
    )
    notifier = NotifierLoop(bot, store, source, settings, screener=screener)

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
