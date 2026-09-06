import asyncio
import logging
import signal

from app.config import Settings
from app.ai import BidGenerator, GeminiClient, OrderScreener
from app.ai.pricing import AtomicQuoteStore
from app.notifier.public import PublicNotifier
from app.rates import RatesProvider
from app.source import FreelancehuntSource
from app.storage.users import UserContext, UserRegistry
from app.telegram import build_bot, build_dispatcher

log = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    registry = UserRegistry(settings)
    await registry.migrate_legacy()
    log.info("public mode: %d registered users", len(await registry.users()))
    bot = build_bot(settings.telegram_bot_token.get_secret_value())
    source = FreelancehuntSource(
        token=settings.freelancehunt_token.get_secret_value(),
        categories=[],
    )

    generators: dict[int, BidGenerator] = {}
    gemini: GeminiClient | None = None
    screener: OrderScreener | None = None
    if settings.ai_active:
        gemini = GeminiClient(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            timeout=settings.gemini_timeout_sec,
        )
        rates_provider = RatesProvider(fallback=settings.fallback_rates)
        # Primary check is fail-open and runs on every new project inside a tick,
        # so it must not block: give it its own client with minimal retries.
        # Bid generation keeps full retries for both automatic and manual bids.
        screen_client = GeminiClient(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            timeout=settings.gemini_timeout_sec,
            max_retries=1,
        )
        screener = OrderScreener(screen_client)
        log.info("ai enabled, model=%s", settings.gemini_model)
    else:
        log.info("ai disabled (no API key or GEMINI_ENABLED=false)")

    def generator_for(user: UserContext) -> BidGenerator | None:
        if gemini is None:
            return None
        if user.user_id not in generators:
            generators[user.user_id] = BidGenerator(
                gemini,
                examples_path=user.prompt_examples_path,
                system_prompt_path=user.system_prompt_path,
                profile_provider=user.store.profile,
                rates_provider=rates_provider,
                quote_store=AtomicQuoteStore(user.quote_path),
            )
        return generators[user.user_id]

    dispatcher = build_dispatcher(settings, registry, generator_for, source)
    notifier = PublicNotifier(bot, registry, source, settings, generator_for, screener)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    polling_task = asyncio.create_task(
        dispatcher.start_polling(bot, handle_signals=False),
        name="updates-polling",
    )
    notifier_task = asyncio.create_task(notifier.run(stop_event), name="notifier")

    stop_task = asyncio.create_task(stop_event.wait(), name="shutdown-wait")
    done, _ = await asyncio.wait(
        (polling_task, notifier_task, stop_task), return_when=asyncio.FIRST_COMPLETED
    )
    stop_event.set()
    log.info("shutdown requested")
    if not polling_task.done():
        await dispatcher.stop_polling()

    for task in (polling_task, notifier_task):
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
        except Exception:
            log.exception("task %s exited with error", task.get_name())

    await asyncio.gather(polling_task, notifier_task, return_exceptions=True)
    stop_task.cancel()

    await source.aclose()
    await bot.session.close()
    # Let Docker restart the service if either critical loop stopped by itself.
    if stop_task not in done:
        raise RuntimeError("a bot service task stopped unexpectedly")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows fallback — not actually used here, but keeps tests happy.
            signal.signal(sig, lambda *_: stop_event.set())
