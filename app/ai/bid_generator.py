import json
import logging
from pathlib import Path
from typing import Literal

from app.llm import ChatMessage, LLMClient, LLMError
from app.projects import Project
from app.rates import RatesProvider

log = logging.getLogger(__name__)

Language = Literal["ru", "ua"]

PROMPTS_DIR = Path(__file__).parent / "prompts"
EXAMPLES_FILE = PROMPTS_DIR / "bids_examples.json"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_prompt.md"

# Used only when no RatesProvider is supplied (e.g. in isolated tests).
_DEFAULT_RATES: dict[str, float] = {"UAH": 43.0, "EUR": 0.92, "PLN": 4.0}

# Чисто украинские буквы — їх відсутність у тексті означає, що це російська/інша мова.
_UA_ONLY_LETTERS = set("іїєґІЇЄҐ")


class BidGenerationError(Exception):
    pass


class BidGenerator:
    """Generates a freelance bid in Nikita's style for a given project."""

    def __init__(
        self,
        client: LLMClient,
        examples_path: Path = EXAMPLES_FILE,
        system_prompt_path: Path = SYSTEM_PROMPT_FILE,
        rates_provider: RatesProvider | None = None,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()
        self._examples = json.loads(examples_path.read_text(encoding="utf-8"))
        self._rates_provider = rates_provider

    async def generate(self, project: Project, language: Language | None = None) -> str:
        lang: Language = language or detect_language(f"{project.title}\n{project.description}")
        rates = await self._rates_provider.get_rates() if self._rates_provider else _DEFAULT_RATES
        messages = self._build_messages(project, lang, rates)
        try:
            return await self._client.generate(
                system_instruction=self._system_prompt,
                messages=messages,
            )
        except LLMError:
            # Quota / rate-limit and other typed LLM failures keep their type so
            # callers can react specifically (e.g. show a "limit reached" notice).
            raise
        except Exception as exc:
            raise BidGenerationError(str(exc)) from exc

    def _build_messages(
        self, project: Project, lang: Language, rates: dict[str, float]
    ) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for example in self._examples.get("examples", []):
            user_payload = json.dumps(example["input"], ensure_ascii=False)
            messages.append(ChatMessage(role="user", text=user_payload))
            messages.append(ChatMessage(role="model", text=example["output"]))

        rates_line = (
            f"1$ = {rates['UAH']:.2f} грн (UAH), {rates['EUR']:.2f} евро (EUR), "
            f"{rates['PLN']:.2f} злотых (PLN)"
        )
        budget_note_ru, budget_note_ua = _budget_floor_notes(project.budget)
        target_payload = {
            "project_title": project.title,
            "project_description": project.description,
            "budget": project.budget or None,
            "language": lang,
            "instructions": (
                "Сгенерируй ставку. Строку цены/сроков выбери по конкретике ТЗ (см. правила): "
                "конкретное ТЗ — реальные числа (часы × 10$, валюта по бюджету заказа); "
                "понятен только тип проекта — оставь литералы {price} и {deadline}; "
                "нет конкретики — убери строку цены/сроков целиком. "
                f"Курсы для перевода из долларов: {rates_line}.{budget_note_ru}"
                if lang == "ru"
                else "Згенеруй ставку. Рядок ціни/термінів обери за конкретикою ТЗ (див. правила): "
                "конкретне ТЗ — реальні числа (години × 10$, валюта за бюджетом замовлення); "
                "зрозумілий лише тип проєкту — залиш літерали {price} і {deadline}; "
                "немає конкретики — прибери рядок ціни/термінів цілком. "
                f"Курси для переведення з доларів: {rates_line}.{budget_note_ua}"
            ),
        }
        messages.append(
            ChatMessage(role="user", text=json.dumps(target_payload, ensure_ascii=False))
        )
        return messages


def parse_budget(raw: str) -> tuple[float | None, str]:
    """Parse a FreelanceHunt budget string like ``"5000 UAH"`` into
    ``(amount, currency)``. Returns ``(None, "")`` when there's no usable amount."""
    if not raw:
        return None, ""
    parts = raw.split()
    try:
        amount = float(parts[0].replace(",", "."))
    except (ValueError, IndexError):
        return None, ""
    currency = parts[1] if len(parts) > 1 else ""
    return amount, currency


def _budget_floor_notes(raw_budget: str) -> tuple[str, str]:
    """Build the (ru, ua) instruction fragments that enforce the budget floor:
    a concrete-ТЗ estimate must never go below the order's own budget. Returns
    empty strings when the order has no parseable budget (nothing to floor to)."""
    amount, currency = parse_budget(raw_budget)
    if amount is None:
        return "", ""
    pretty = f"{amount:.0f} {currency}".strip()
    ru = (
        f" Бюджет заказа — {pretty}. Это применимо ТОЛЬКО в варианте 1 (конкретное ТЗ, "
        "считаешь реальные числа): если расчёт (часы × 10$) в пересчёте на валюту "
        "заказа выходит меньше бюджета — ставь сумму бюджета, ниже бюджета клиента "
        "не опускайся. В вариантах 2 и 3 (литералы цены или строка цены убрана) бюджет "
        "сюда не подставляй."
    )
    ua = (
        f" Бюджет замовлення — {pretty}. Це застосовно ТІЛЬКИ у варіанті 1 (конкретне ТЗ, "
        "рахуєш реальні числа): якщо розрахунок (години × 10$) у перерахунку на валюту "
        "замовлення виходить менший за бюджет — став суму бюджету, нижче за бюджет клієнта "
        "не опускайся. У варіантах 2 і 3 (літерали ціни або рядок ціни прибрано) бюджет "
        "сюди не підставляй."
    )
    return ru, ua


def detect_language(text: str) -> Language:
    if not text:
        return "ru"
    for ch in text:
        if ch in _UA_ONLY_LETTERS:
            return "ua"
    return "ru"
