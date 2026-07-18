import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.ai.bid_generator import BidGenerationError, BidGenerator
from app.ai.pricing import (
    AtomicQuoteStore,
    LLMScopeEstimator,
    PricingEngine,
    PricingQuote,
    QuoteService,
    StaticRatesSource,
    format_quote_line,
    normalize_dashes,
    parse_budget,
    render_bid,
)
from app.llm import LLMError
from app.projects import Project


def _project(*, project_id: str = "project-1", budget: str = "") -> Project:
    return Project(
        id=project_id,
        url="https://example.com/project-1",
        title="Telegram bot",
        budget=budget,
        description="Bot with admin panel and Google Calendar integration",
        relative_time="",
        absolute_time="",
        published_ts=1,
    )


class _SequenceEstimator:
    def __init__(self, *tiers: str) -> None:
        self._tiers = list(tiers)
        self.calls = 0

    async def estimate(self, project: Project) -> str:
        tier = self._tiers[min(self.calls, len(self._tiers) - 1)]
        self.calls += 1
        return tier


class _SequenceRates:
    def __init__(self, *uah_rates: str) -> None:
        self._rates = list(uah_rates)
        self.calls = 0

    async def get_rates(self) -> dict[str, Decimal]:
        uah = self._rates[min(self.calls, len(self._rates) - 1)]
        self.calls += 1
        return {"UAH": Decimal(uah), "EUR": Decimal("0.92"), "PLN": Decimal("4")}


class _SequenceClient:
    def __init__(self, *responses: str, error: Exception | None = None) -> None:
        self._responses = list(responses)
        self._error = error
        self.calls: list[dict] = []

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list,
        temperature: float = 0.8,
    ) -> str:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "messages": messages,
                "temperature": temperature,
            }
        )
        if self._error is not None:
            raise self._error
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]


class PricingEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PricingEngine(Decimal("12"))
        self.rates = {
            "UAH": Decimal("43"),
            "EUR": Decimal("0.92"),
            "PLN": Decimal("4"),
        }

    def test_rate_rounds_up_then_applies_exact_client_floor(self) -> None:
        quote = self.engine.quote(_project(budget="5251 UAH"), "8", self.rates)

        # 8 * 12 * 43 = 4128, rounded up to 4500, then exact floor wins.
        self.assertEqual(quote.amount, Decimal("5251"))
        self.assertEqual(quote.currency, "UAH")
        self.assertEqual(quote.hourly_rate_usd, Decimal("12"))

    def test_rounding_steps_for_every_supported_currency(self) -> None:
        cases = [
            ("", "UAH", Decimal("2500")),
            ("1 USD", "USD", Decimal("50")),
            ("1 EUR", "EUR", Decimal("50")),
            ("1 PLN", "PLN", Decimal("200")),
        ]
        for budget, currency, expected in cases:
            with self.subTest(currency=currency):
                quote = self.engine.quote(_project(budget=budget), "4", self.rates)
                self.assertEqual(quote.currency, currency)
                self.assertEqual(quote.amount, expected)

    def test_floor_is_not_rounded_again(self) -> None:
        quote = self.engine.quote(_project(budget="105 USD"), "8", self.rates)

        self.assertEqual(quote.amount, Decimal("105"))

    def test_missing_or_unsupported_budget_currency_uses_uah(self) -> None:
        for budget in ("", "100 GBP", "not a budget"):
            with self.subTest(budget=budget):
                quote = self.engine.quote(_project(budget=budget), "4", self.rates)
                self.assertEqual(quote.currency, "UAH")
                self.assertEqual(quote.amount, Decimal("2500"))

    def test_deadline_is_ceil_hours_over_eight_plus_one_day(self) -> None:
        expected = {
            "4": "1-2",
            "8": "1-2",
            "16": "2-3",
            "24": "3-4",
            "40": "5-6",
            "80": "10-11",
        }
        for tier, deadline in expected.items():
            with self.subTest(tier=tier):
                quote = self.engine.quote(_project(), tier, self.rates)
                self.assertEqual(quote.deadline, deadline)

    def test_omit_has_no_price_or_deadline(self) -> None:
        quote = self.engine.quote(_project(budget="9999 UAH"), "omit", self.rates)

        self.assertTrue(quote.omitted)
        self.assertIsNone(quote.hours)
        self.assertIsNone(quote.amount)
        self.assertIsNone(quote.currency)
        self.assertIsNone(quote.deadline)

    def test_budget_parser_uses_decimal_and_rejects_invalid_values(self) -> None:
        self.assertEqual(parse_budget("5 251,75 UAH"), (Decimal("5251.75"), "UAH"))
        self.assertEqual(parse_budget("105 usd"), (Decimal("105"), "USD"))
        self.assertEqual(parse_budget("0 USD"), (None, ""))
        self.assertEqual(parse_budget("unknown"), (None, ""))


class RenderingTest(unittest.TestCase):
    def _quote(self, tier: str = "4") -> PricingQuote:
        return PricingEngine().quote(
            _project(),
            tier,
            {"UAH": Decimal("43"), "EUR": Decimal("0.92"), "PLN": Decimal("4")},
        )

    def test_concrete_render_removes_model_pricing_and_normalizes_dashes(self) -> None:
        prose = (
            "Привет — готов сделать проект\n"
            "Цена: 999 USD, срок 2 дня\n"
            "Сделаю за 3 дня\n"
            "По срокам всё будет быстро\n"
            "Пишите ‐‑‒–—―−-- обсудим"
        )

        rendered = render_bid(prose, self._quote(), "ru")

        self.assertIn("Привет - готов сделать проект", rendered)
        self.assertIn("Ориентировочные цена, сроки: 2500 грн, 1-2 календарных дня.", rendered)
        self.assertNotIn("999", rendered)
        self.assertNotIn("3 дня", rendered)
        self.assertNotIn("По срокам", rendered)
        for dash in "‐‑‒–—―−":
            self.assertNotIn(dash, rendered)
        self.assertNotIn("--", rendered)

    def test_concrete_render_removes_text_only_duration_promises(self) -> None:
        prose = (
            "Привет, готов взяться за проект\n"
            "Сделаю за неделю\n"
            "Управлюсь за пару дней\n"
            "Виконаю за тиждень\n"
            "Розробка займе кілька місяців\n"
            "Похожую механику уже собирал\n"
            "Пишите, обсудим детали"
        )

        rendered = render_bid(prose, self._quote(), "ru")

        self.assertNotIn("Сделаю за неделю", rendered)
        self.assertNotIn("Управлюсь за пару дней", rendered)
        self.assertNotIn("Виконаю за тиждень", rendered)
        self.assertNotIn("займе кілька місяців", rendered)
        self.assertIn("Похожую механику уже собирал", rendered)
        self.assertIn("Пишите, обсудим детали", rendered)
        self.assertEqual(
            rendered.splitlines()[-1],
            "Ориентировочные цена, сроки: 2500 грн, 1-2 календарных дня.",
        )

    def test_concrete_render_preserves_domain_price_nouns(self) -> None:
        prose = (
            "Сделаю парсер цен\n"
            "Настрою мониторинг цен с 3 сайтов\n"
            "Автоматизирую обновление цен каждый час\n"
            "Пишите, обсудим детали"
        )

        rendered = render_bid(prose, self._quote(), "ru")

        self.assertIn("Сделаю парсер цен", rendered)
        self.assertIn("Настрою мониторинг цен с 3 сайтов", rendered)
        self.assertIn("Автоматизирую обновление цен каждый час", rendered)
        self.assertIn("Пишите, обсудим детали", rendered)

    def test_concrete_render_removes_contextual_pricing_discussion(self) -> None:
        prose = (
            "Готов выполнить задачу\n"
            "Цена: 5000\n"
            "Стоимость будет зависеть от деталей\n"
            "По стоимости обсудим в личке\n"
            "Обсудим цену после старта\n"
            "Щодо вартості обговоримо окремо\n"
            "Предлагаю 5000\n"
            "Пишите, обсудим детали"
        )

        rendered = render_bid(prose, self._quote(), "ru")

        self.assertIn("Готов выполнить задачу", rendered)
        self.assertIn("Пишите, обсудим детали", rendered)
        self.assertNotIn("Цена: 5000", rendered)
        self.assertNotIn("Стоимость будет", rendered)
        self.assertNotIn("По стоимости", rendered)
        self.assertNotIn("Обсудим цену", rendered)
        self.assertNotIn("Щодо вартості", rendered)
        self.assertNotIn("Предлагаю 5000", rendered)
        self.assertEqual(
            rendered.splitlines()[-1],
            "Ориентировочные цена, сроки: 2500 грн, 1-2 календарных дня.",
        )

    def test_vague_render_removes_questions_and_adds_neutral_invitation(self) -> None:
        prose = (
            "Привет, готов помочь?\n"
            "Пришлите подробности задачи\n"
            "Похожую механику уже собирал -- проблем не будет\n"
            "Пишите, обсудим"
        )

        rendered = render_bid(prose, self._quote("omit"), "ru")

        self.assertEqual(
            rendered,
            "Похожую механику уже собирал - проблем не будет\n\n"
            "Пишите, обсудим детали в личке",
        )
        self.assertNotIn("?", rendered)

    def test_quote_line_uses_one_fixed_number(self) -> None:
        line = format_quote_line(self._quote(), "ru")

        self.assertEqual(line, "Ориентировочные цена, сроки: 2500 грн, 1-2 календарных дня.")

    def test_every_supported_unicode_dash_collapses_to_ascii(self) -> None:
        self.assertEqual(normalize_dashes("a‐‑‒–—―−---b"), "a-b")


class QuotePersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_store_is_first_write_wins_and_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotes.json"
            store = AtomicQuoteStore(path)
            first = {"tier": "4"}
            second = {"tier": "80"}

            self.assertEqual(
                await store.save_pricing_quote_if_absent("project-1", first), first
            )
            self.assertEqual(
                await store.save_pricing_quote_if_absent("project-1", second), first
            )
            self.assertEqual(
                await AtomicQuoteStore(path).pricing_quote("project-1"), first
            )
            self.assertEqual(json.loads(path.read_text())["quotes"]["project-1"], first)

    async def test_regeneration_reuses_quote_even_if_new_estimate_and_fx_differ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            estimator = _SequenceEstimator("16", "80")
            rates = _SequenceRates("43", "50")
            client = _SequenceClient(
                "Первый вариант — готов выполнить задачу",
                "Второй вариант -- похожее уже делал",
            )
            generator = BidGenerator(
                client,
                quote_store=AtomicQuoteStore(Path(tmp) / "quotes.json"),
                scope_estimator=estimator,
                rates_provider=rates,
            )

            first = await generator.generate(_project())
            second = await generator.generate(_project())

            self.assertNotEqual(first.splitlines()[0], second.splitlines()[0])
            self.assertEqual(first.splitlines()[-1], second.splitlines()[-1])
            self.assertEqual(estimator.calls, 1)
            self.assertEqual(rates.calls, 1)
            self.assertIn("8500 грн, 2-3", first)
            self.assertNotIn("—", first)
            self.assertNotIn("--", second)
            target = json.loads(client.calls[0]["messages"][-1].text)
            self.assertNotIn("budget", target)
            self.assertEqual(target["scope"], "concrete")

    async def test_quote_is_persisted_before_prose_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotes.json"
            generator = BidGenerator(
                _SequenceClient(error=LLMError("prose failed")),
                quote_store=AtomicQuoteStore(path),
                scope_estimator=_SequenceEstimator("8"),
                rates_provider=StaticRatesSource(),
            )

            with self.assertRaises(BidGenerationError):
                await generator.generate(_project())

            stored = await AtomicQuoteStore(path).pricing_quote("project-1")
            self.assertIsNotNone(stored)
            self.assertEqual(stored["tier"], "8")

    async def test_scope_estimator_uses_zero_temperature(self) -> None:
        client = _SequenceClient("24")

        tier = await LLMScopeEstimator(client).estimate(_project())

        self.assertEqual(tier, "24")
        self.assertEqual(client.calls[0]["temperature"], 0.0)


if __name__ == "__main__":
    unittest.main()
