from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Protocol

from app.llm import ChatMessage, LLMClient, LLMResponseError
from app.projects import Project

log = logging.getLogger(__name__)

ScopeTier = Literal["omit", "4", "8", "16", "24", "40", "80"]
Language = Literal["ru", "ua"]

ALLOWED_HOURS = (4, 8, 16, 24, 40, 80)
ALLOWED_TIERS = {"omit", *(str(hours) for hours in ALLOWED_HOURS)}
POLICY_VERSION = "quote-v1"
DEFAULT_HOURLY_RATE_USD = Decimal("12")
DEFAULT_RATES = {"UAH": Decimal("43"), "EUR": Decimal("0.92"), "PLN": Decimal("4")}
SCOPE_PROMPT_FILE = Path(__file__).parent / "prompts" / "scope_prompt.md"

_STEPS = {
    "UAH": Decimal("500"),
    "USD": Decimal("10"),
    "EUR": Decimal("10"),
    "PLN": Decimal("50"),
}
_CURRENCY_LABELS = {"UAH": "грн", "USD": "$", "EUR": "€", "PLN": "zł"}
_DASH_TRANSLATION = str.maketrans(
    {character: "-" for character in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
)
_CURRENCY_OR_PLACEHOLDER_RE = re.compile(
    r"(?i)(\{price\}|\{deadline\}|\b(?:UAH|USD|EUR|PLN)\b|"
    r"грн|доллар|долар|евро|євро|злот|zł|[$€])"
)
_QUOTE_LABEL_RE = re.compile(
    r"(?i)^\s*(?:ориентировоч|орієнтовн|цена\b|ціна\b|стоимость\b|"
    r"вартість\b|бюджет\b|срок\b|строк\b|термін\b|price\b|deadline\b)"
)
_DEADLINE_TOPIC_RE = re.compile(
    r"(?i)\b(?:срок|сроки|сроков|срокам|сроках|сроком|"
    r"строк|строки|строків|строкам|строках|строком|"
    r"термін|терміни|термінів|термінам|термінах|терміном|"
    r"deadline)\b"
)
_PRICING_DISCUSSION_RE = re.compile(
    r"(?i)\b(?:по|щодо)\s+(?:цене|ценам|стоимости|вартості|бюджету)\b|"
    r"\b(?:что\s+касается|що\s+стосується)\s+"
    r"(?:цены|цiни|ціни|стоимости|вартості|бюджета|бюджету)\b|"
    r"\b(?:обсудим|согласуем|уточним|обговоримо|узгодимо|уточнимо)\s+"
    r"(?:цену|цiну|ціну|стоимость|вартість|бюджет)\b|"
    r"\b(?:цену|цiну|ціну|стоимость|вартість|бюджет)\s+"
    r"(?:обсудим|согласуем|уточним|обговоримо|узгодимо|уточнимо)\b"
)
_NUMERIC_QUOTE_RE = re.compile(
    r"(?i)^\s*(?:(?:ориентировочно|орієнтовно|примерно|приблизно|"
    r"предлагаю|пропоную|ставка)\s*[:=-]?\s*)?\d+(?:[.,]\d+)?\s*$"
)
_NUMBERED_TIME_RE = re.compile(
    r"(?i)\d\s*(?:-|до\s*)?\s*\d*\s*(?:час|годин|дн|день|дня|дней|дні|днів|day)"
)
_TIME_UNIT_PATTERN = (
    r"(?:час|часа|часов|часу|часами|"
    r"год|года|годов|година|години|годину|годин|годинами|"
    r"день|дня|дней|дни|дні|днів|днями|"
    r"неделя|неделю|недели|недель|неделей|"
    r"тиждень|тижня|тижні|тижнів|тижнем|тижнями|"
    r"месяц|месяца|месяцы|месяцев|месяцем|месяцами|"
    r"місяць|місяця|місяці|місяців|місяцем|місяцями)"
)
_DURATION_PHRASE_RE = re.compile(
    rf"(?i)\b(?:за|на)\s+(?:(?:пару|кілька|несколько|один|одну|одна|"
    rf"два|две|дві|три|четыре|чотири|\d+)\s+)?{_TIME_UNIT_PATTERN}\b|"
    rf"\b(?:в\s+течение|на\s+протяжении|протягом|впродовж)\s+"
    rf"(?:(?:пары|кількох|нескольких|одного|однієї|\d+)\s+)?{_TIME_UNIT_PATTERN}\b"
)
_COMPLETION_TIME_RE = re.compile(
    rf"(?i)(?:сдела|выполн|заверш|закончи|управ|справ|реализ|разработ|"
    rf"займ[её]|потребу|викона|закінч|встиг|реаліз|розроб).{{0,80}}"
    rf"\b{_TIME_UNIT_PATTERN}\b|"
    rf"\b{_TIME_UNIT_PATTERN}\b.{{0,80}}(?:сдела|выполн|заверш|закончи|"
    rf"управ|справ|реализ|разработ|займ[её]|потребу|викона|закінч|встиг|"
    rf"реаліз|розроб)"
)
_QUESTION_RE = re.compile(r"\?")
_CLARIFICATION_RE = re.compile(
    r"(?i)(уточн|расскаж|опишит|пришлит|какие\s+|подробност|"
    r"уточнi|уточні|розкаж|опишi|опиші|надiшл|надішл|якi\s+|які\s+|детал)"
)
_CLOSING_RE = re.compile(
    r"(?i)^\s*(пиш|напиш|можем\s+обсуд|готов\s+обсуд|звертай|"
    r"пишi|пиші|обговор|готовий\s+обговор)"
)


class ScopeEstimator(Protocol):
    async def estimate(self, project: Project) -> ScopeTier:
        ...


class QuoteStore(Protocol):
    async def pricing_quote(self, project_id: str) -> dict | None:
        ...

    async def save_pricing_quote_if_absent(
        self, project_id: str, quote: dict
    ) -> dict:
        ...

    async def replace_pricing_quote(self, project_id: str, quote: dict) -> None:
        ...


class RatesSource(Protocol):
    async def get_rates(self) -> dict[str, float]:
        ...


@dataclass(frozen=True)
class PricingQuote:
    tier: ScopeTier
    hours: int | None
    amount: Decimal | None
    currency: str | None
    deadline: str | None
    fx_snapshot: dict[str, str]
    hourly_rate_usd: Decimal
    policy_version: str
    input_fingerprint: str

    @property
    def omitted(self) -> bool:
        return self.tier == "omit"

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "hours": self.hours,
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "deadline": self.deadline,
            "fx_snapshot": dict(self.fx_snapshot),
            "hourly_rate_usd": str(self.hourly_rate_usd),
            "policy_version": self.policy_version,
            "input_fingerprint": self.input_fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "PricingQuote":
        tier = str(raw.get("tier", ""))
        if tier not in ALLOWED_TIERS:
            raise ValueError("invalid pricing tier")
        hours = None if tier == "omit" else int(tier)
        raw_amount = raw.get("amount")
        amount = None if raw_amount is None else Decimal(str(raw_amount))
        currency = raw.get("currency")
        deadline = raw.get("deadline")
        if tier == "omit":
            if amount is not None or currency is not None or deadline is not None:
                raise ValueError("omitted quote contains pricing values")
        elif amount is None or currency not in _STEPS or not isinstance(deadline, str):
            raise ValueError("priced quote is incomplete")
        fx_snapshot = raw.get("fx_snapshot")
        if not isinstance(fx_snapshot, dict):
            raise ValueError("invalid FX snapshot")
        return cls(
            tier=tier,  # type: ignore[arg-type]
            hours=hours,
            amount=amount,
            currency=currency,
            deadline=deadline,
            fx_snapshot={str(key): str(value) for key, value in fx_snapshot.items()},
            hourly_rate_usd=Decimal(str(raw.get("hourly_rate_usd"))),
            policy_version=str(raw.get("policy_version") or ""),
            input_fingerprint=str(raw.get("input_fingerprint") or ""),
        )


class LLMScopeEstimator:
    def __init__(
        self,
        client: LLMClient,
        prompt_path: Path = SCOPE_PROMPT_FILE,
    ) -> None:
        self._client = client
        self._prompt = prompt_path.read_text(encoding="utf-8").strip()

    async def estimate(self, project: Project) -> ScopeTier:
        payload = json.dumps(
            {"title": project.title, "description": project.description},
            ensure_ascii=False,
        )
        raw = await self._client.generate(
            system_instruction=self._prompt,
            messages=[ChatMessage(role="user", text=payload)],
            temperature=0.0,
        )
        match = re.search(r"(?<!\d)(omit|80|40|24|16|8|4)(?!\d)", raw.lower())
        if match is None:
            raise LLMResponseError(f"invalid scope tier: {raw[:100]!r}")
        return match.group(1)  # type: ignore[return-value]


class InMemoryQuoteStore:
    def __init__(self) -> None:
        self._quotes: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def pricing_quote(self, project_id: str) -> dict | None:
        async with self._lock:
            quote = self._quotes.get(project_id)
            return copy.deepcopy(quote) if quote is not None else None

    async def save_pricing_quote_if_absent(
        self, project_id: str, quote: dict
    ) -> dict:
        async with self._lock:
            stored = self._quotes.setdefault(project_id, copy.deepcopy(quote))
            return copy.deepcopy(stored)

    async def replace_pricing_quote(self, project_id: str, quote: dict) -> None:
        async with self._lock:
            self._quotes[project_id] = copy.deepcopy(quote)


class AtomicQuoteStore:
    """Dedicated, rollback-safe quote persistence with atomic file replacement."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._quotes: dict[str, dict] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def pricing_quote(self, project_id: str) -> dict | None:
        async with self._lock:
            await self._ensure_loaded_locked()
            quote = self._quotes.get(project_id)
            return copy.deepcopy(quote) if quote is not None else None

    async def save_pricing_quote_if_absent(
        self, project_id: str, quote: dict
    ) -> dict:
        async with self._lock:
            await self._ensure_loaded_locked()
            stored = self._quotes.get(project_id)
            if stored is None:
                self._quotes[project_id] = copy.deepcopy(quote)
                await self._persist_locked()
                stored = quote
            return copy.deepcopy(stored)

    async def replace_pricing_quote(self, project_id: str, quote: dict) -> None:
        async with self._lock:
            await self._ensure_loaded_locked()
            self._quotes[project_id] = copy.deepcopy(quote)
            await self._persist_locked()

    async def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            quotes = data.get("quotes") if isinstance(data, dict) else None
            if not isinstance(quotes, dict):
                raise ValueError("quotes root is missing")
            self._quotes = {
                str(project_id): copy.deepcopy(quote)
                for project_id, quote in quotes.items()
                if isinstance(quote, dict)
            }
        except Exception:
            log.exception("failed to load pricing quotes from %s", self._path)
            self._quotes = {}

    async def _persist_locked(self) -> None:
        payload = json.dumps(
            {"quotes": self._quotes}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        # The file is deliberately small and the async lock already serializes
        # writers. A direct atomic replace is more reliable here than handing an
        # fsync operation to a worker thread during event-loop shutdown.
        _atomic_write(self._path, payload)


class PricingEngine:
    def __init__(self, hourly_rate_usd: Decimal = DEFAULT_HOURLY_RATE_USD) -> None:
        if hourly_rate_usd <= 0:
            raise ValueError("hourly rate must be positive")
        self._hourly_rate_usd = Decimal(hourly_rate_usd)

    @property
    def hourly_rate_usd(self) -> Decimal:
        return self._hourly_rate_usd

    def quote(
        self,
        project: Project,
        tier: ScopeTier,
        rates: dict[str, float | Decimal],
    ) -> PricingQuote:
        fingerprint = input_fingerprint(project)
        fx = _fx_snapshot(rates)
        if tier == "omit":
            return PricingQuote(
                tier=tier,
                hours=None,
                amount=None,
                currency=None,
                deadline=None,
                fx_snapshot=fx,
                hourly_rate_usd=self._hourly_rate_usd,
                policy_version=POLICY_VERSION,
                input_fingerprint=fingerprint,
            )

        hours = int(tier)
        budget_amount, budget_currency = parse_budget(project.budget)
        currency = budget_currency if budget_currency in _STEPS else "UAH"
        rate = Decimal("1") if currency == "USD" else Decimal(fx[currency])
        raw = Decimal(hours) * self._hourly_rate_usd * rate
        rounded_raw = round_up(raw, _STEPS[currency])
        exact_floor = (
            budget_amount
            if budget_amount is not None and budget_currency == currency
            else Decimal("0")
        )
        amount = max(rounded_raw, exact_floor)
        base_days = max(1, (hours + 7) // 8)
        return PricingQuote(
            tier=tier,
            hours=hours,
            amount=amount,
            currency=currency,
            deadline=f"{base_days}-{base_days + 1}",
            fx_snapshot=fx,
            hourly_rate_usd=self._hourly_rate_usd,
            policy_version=POLICY_VERSION,
            input_fingerprint=fingerprint,
        )


class QuoteService:
    def __init__(
        self,
        estimator: ScopeEstimator,
        engine: PricingEngine,
        store: QuoteStore,
        rates: RatesSource,
    ) -> None:
        self._estimator = estimator
        self._engine = engine
        self._store = store
        self._rates = rates
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def get_or_create(self, project: Project) -> PricingQuote:
        lock = await self._project_lock(project.id)
        async with lock:
            stored = await self._store.pricing_quote(project.id)
            if stored is not None:
                try:
                    return PricingQuote.from_dict(stored)
                except (InvalidOperation, TypeError, ValueError):
                    pass

            tier = await self._estimator.estimate(project)
            rates = await self._rates.get_rates()
            quote = self._engine.quote(project, tier, rates)
            if stored is None:
                saved = await self._store.save_pricing_quote_if_absent(
                    project.id, quote.to_dict()
                )
                return PricingQuote.from_dict(saved)
            await self._store.replace_pricing_quote(project.id, quote.to_dict())
            return quote

    async def _project_lock(self, project_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(project_id, asyncio.Lock())


class StaticRatesSource:
    def __init__(self, rates: dict[str, float | Decimal] | None = None) -> None:
        self._rates = rates or DEFAULT_RATES

    async def get_rates(self) -> dict[str, float | Decimal]:
        return dict(self._rates)


def round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def parse_budget(raw: str) -> tuple[Decimal | None, str]:
    text = raw.strip().replace("\u00a0", " ")
    if not text:
        return None, ""
    match = re.fullmatch(r"(.+?)\s+([A-Za-z]{3})", text)
    if match:
        amount_text, currency = match.groups()
    else:
        amount_text, currency = text, ""
    normalized = amount_text.replace(" ", "").replace("_", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None, ""
    if amount <= 0:
        return None, ""
    return amount, currency.upper()


def input_fingerprint(project: Project) -> str:
    payload = json.dumps(
        {
            "title": project.title.strip(),
            "description": project.description.strip(),
            "budget": project.budget.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_dashes(text: str) -> str:
    normalized = text.translate(_DASH_TRANSLATION)
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized


def sanitize_prose(text: str, *, vague: bool) -> str:
    kept: list[str] = []
    for raw_line in normalize_dashes(text).splitlines():
        line = raw_line.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if contains_quote_leakage(line):
            continue
        if vague and (
            _QUESTION_RE.search(line)
            or _CLARIFICATION_RE.search(line)
            or _CLOSING_RE.search(line)
        ):
            continue
        kept.append(line)
    while kept and kept[-1] == "":
        kept.pop()
    return "\n".join(kept).strip()


def contains_quote_leakage(line: str) -> bool:
    return bool(
        _CURRENCY_OR_PLACEHOLDER_RE.search(line)
        or _QUOTE_LABEL_RE.search(line)
        or _DEADLINE_TOPIC_RE.search(line)
        or _PRICING_DISCUSSION_RE.search(line)
        or _NUMERIC_QUOTE_RE.search(line)
        or _NUMBERED_TIME_RE.search(line)
        or _DURATION_PHRASE_RE.search(line)
        or _COMPLETION_TIME_RE.search(line)
    )


def render_bid(prose: str, quote: PricingQuote, language: Language) -> str:
    body = sanitize_prose(prose, vague=quote.omitted)
    if not body:
        raise ValueError("model prose is empty after pricing sanitization")
    if quote.omitted:
        closing = (
            "Пишите, обсудим детали в личке"
            if language == "ru"
            else "Пишіть, обговоримо деталі в особистих повідомленнях"
        )
        return normalize_dashes(f"{body}\n\n{closing}")
    return normalize_dashes(f"{body}\n\n{format_quote_line(quote, language)}")


def format_quote_line(quote: PricingQuote, language: Language) -> str:
    if quote.omitted or quote.amount is None or quote.currency is None or quote.deadline is None:
        raise ValueError("cannot render omitted quote")
    amount = _format_decimal(quote.amount)
    currency = _CURRENCY_LABELS[quote.currency]
    start, end = (int(item) for item in quote.deadline.split("-", 1))
    days = _calendar_days(start, end, language)
    prefix = "Ориентировочные цена, сроки" if language == "ru" else "Орієнтовні ціна, строки"
    return f"{prefix}: {amount} {currency}, {quote.deadline} {days}."


def _fx_snapshot(rates: dict[str, float | Decimal]) -> dict[str, str]:
    snapshot = {"USD": "1"}
    for currency in ("UAH", "EUR", "PLN"):
        value = rates.get(currency, DEFAULT_RATES[currency])
        decimal = Decimal(str(value))
        if decimal <= 0:
            decimal = DEFAULT_RATES[currency]
        snapshot[currency] = str(decimal)
    return snapshot


def _format_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _calendar_days(start: int, end: int, language: Language) -> str:
    if language == "ua":
        noun = "календарні дні" if end % 10 in (2, 3, 4) and end % 100 not in (12, 13, 14) else "календарних днів"
    else:
        noun = "календарных дня" if end % 10 in (2, 3, 4) and end % 100 not in (12, 13, 14) else "календарных дней"
    return noun


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
