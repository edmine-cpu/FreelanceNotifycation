import json
import logging
from pathlib import Path

from app.llm import ChatMessage, LLMClient, QuotaExceededError
from app.projects import Project

from .pricing import (
    DEFAULT_HOURLY_RATE_USD,
    InMemoryQuoteStore,
    Language,
    LLMScopeEstimator,
    PricingEngine,
    QuoteService,
    QuoteStore,
    RatesSource,
    ScopeEstimator,
    StaticRatesSource,
    render_bid,
)
from .prompt_store import sanitize_prompt_data

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
EXAMPLES_FILE = PROMPTS_DIR / "bids_examples.json"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_prompt.md"

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
        rates_provider: RatesSource | None = None,
        quote_store: QuoteStore | None = None,
        scope_estimator: ScopeEstimator | None = None,
        pricing_engine: PricingEngine | None = None,
    ) -> None:
        self._client = client
        self._system_prompt_path = system_prompt_path
        self._examples_path = examples_path
        self._quotes = QuoteService(
            estimator=scope_estimator or LLMScopeEstimator(client),
            engine=pricing_engine or PricingEngine(DEFAULT_HOURLY_RATE_USD),
            store=quote_store or InMemoryQuoteStore(),
            rates=rates_provider or StaticRatesSource(),
        )
        self.reload_prompt()

    def reload_prompt(self) -> None:
        self._system_prompt = self._system_prompt_path.read_text(encoding="utf-8").strip()
        raw_examples = json.loads(self._examples_path.read_text(encoding="utf-8"))
        self._examples = sanitize_prompt_data(raw_examples)

    async def generate(self, project: Project, language: Language | None = None) -> str:
        lang: Language = language or detect_language(f"{project.title}\n{project.description}")
        try:
            # Persist the deterministic quote before asking for disposable prose.
            quote = await self._quotes.get_or_create(project)
            messages = self._build_messages(project, lang, vague=quote.omitted)
            prose = await self._client.generate(
                system_instruction=self._system_prompt,
                messages=messages,
            )
            return render_bid(prose, quote, lang)
        except QuotaExceededError:
            # Keep quota/rate-limit separate so callers can show a precise notice.
            raise
        except Exception as exc:
            raise BidGenerationError(str(exc)) from exc

    def _build_messages(
        self, project: Project, lang: Language, *, vague: bool
    ) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for example in self._examples.get("examples", []):
            user_payload = json.dumps(example["input"], ensure_ascii=False)
            messages.append(ChatMessage(role="user", text=user_payload))
            messages.append(ChatMessage(role="model", text=example["output"]))

        target_payload = {
            "project_title": project.title,
            "project_description": project.description,
            "language": lang,
            "scope": "vague" if vague else "concrete",
            "instructions": "Напиши только текст отклика по правилам. Не считай и не упоминай цену, бюджет, часы или сроки.",
        }
        messages.append(
            ChatMessage(role="user", text=json.dumps(target_payload, ensure_ascii=False))
        )
        return messages


def detect_language(text: str) -> Language:
    if not text:
        return "ru"
    for ch in text:
        if ch in _UA_ONLY_LETTERS:
            return "ua"
    return "ru"
