import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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
PUBLIC_SYSTEM_PROMPT_FILE = PROMPTS_DIR / "public_system_prompt.md"

ProfileProvider = Callable[[], Awaitable[dict[str, str]]]

# Old examples remain available for migration, but their identity must never
# override a user's current profile (including an explicitly empty profile).
_LEGACY_IDENTITY_RE = re.compile(
    r"\b(?:Никита|Микита|Nikita)\b|freelancehunt\.com/freelancer/edmine\b",
    re.IGNORECASE,
)
_PERSONAL_IDENTITY_RE = re.compile(
    r"(?i:\b(?:меня\s+зовут|мене\s+звати|мо[её]\s+имя|моє\s+ім'я|my\s+name\s+is)\b"
    r"|^\s*(?:(?:ссылка|посилання)\s+на\s+)?(?:портфол[иі]о|portfolio)\s*[:\-]"
    r"|\b(?:мо[еёє]|my)\s+(?:портфол[иі]о|portfolio)\b"
    r"|^\s*(?:мои\s+работы|мої\s+роботи|my\s+work)\s*[:\-]"
    r"|^\s*(?:с\s+уважением|з\s+повагою|regards)[,\s])"
    r"|(?:^|[,!?.]\s*)(?:[Яя]|I am|I'm)\s+[A-ZА-ЯІЇЄҐ][a-zа-яіїєґ]+\b"
)

# Чисто украинские буквы — їх відсутність у тексті означає, що це російська/інша мова.
_UA_ONLY_LETTERS = set("іїєґІЇЄҐ")


class BidGenerationError(Exception):
    pass


class BidGenerator:
    """Generate a bid using one user's current prompt, examples and profile."""

    def __init__(
        self,
        client: LLMClient,
        examples_path: Path | None = None,
        system_prompt_path: Path = PUBLIC_SYSTEM_PROMPT_FILE,
        rates_provider: RatesSource | None = None,
        quote_store: QuoteStore | None = None,
        scope_estimator: ScopeEstimator | None = None,
        pricing_engine: PricingEngine | None = None,
        profile_provider: ProfileProvider | None = None,
    ) -> None:
        self._client = client
        self._system_prompt_path = system_prompt_path
        self._examples_path = examples_path
        self._profile_provider = profile_provider
        self._quotes = QuoteService(
            estimator=scope_estimator or LLMScopeEstimator(client),
            engine=pricing_engine or PricingEngine(DEFAULT_HOURLY_RATE_USD),
            store=quote_store or InMemoryQuoteStore(),
            rates=rates_provider or StaticRatesSource(),
        )
        self.reload_prompt()

    def reload_prompt(self) -> None:
        """Validate edits; generation always reads its own fresh snapshot."""
        self._read_prompt()

    def _read_prompt(self) -> tuple[str, dict[str, Any]]:
        system_prompt = self._system_prompt_path.read_text(encoding="utf-8").strip()
        raw_examples = (
            json.loads(self._examples_path.read_text(encoding="utf-8"))
            if self._examples_path is not None
            else {"examples": []}
        )
        return system_prompt, sanitize_prompt_data(raw_examples)

    async def generate(self, project: Project, language: Language | None = None) -> str:
        lang: Language = language or detect_language(f"{project.title}\n{project.description}")
        try:
            profile = None
            if self._profile_provider is not None:
                supplied = await self._profile_provider()
                profile = {
                    key: supplied.get(key, "").strip()
                    for key in ("name", "portfolio_url")
                }
            system_prompt, examples = self._read_prompt()
            if profile is not None:
                # Keep the user's style instructions, including instructions
                # about portfolio projects. Only known migration identity is
                # removed here; the current profile explicitly overrides all
                # other identity instructions below.
                system_prompt = "\n".join(
                    line for line in system_prompt.splitlines()
                    if not _LEGACY_IDENTITY_RE.search(line)
                ).strip()
                for example in examples["examples"]:
                    example["output"] = _without_personal_identity(example["output"])
                system_prompt += (
                    "\n\nПрофиль автора ниже - единственный источник имени и ссылки на "
                    "портфолио. Не переноси личные данные из примеров или описания проекта. "
                    "Пустое значение означает, что данные не указаны; не придумывай их. "
                    "Напиши только содержательную часть отклика, без представления, "
                    "имени автора, подписи и портфолио: программа добавит точные данные "
                    "профиля сама. Не придумывай опыт, клиентов и выполненные работы.\n"
                    + json.dumps(profile, ensure_ascii=False)
                )
            # Persist the deterministic quote before asking for disposable prose.
            quote = await self._quotes.get_or_create(project)
            messages = self._build_messages(project, lang, vague=quote.omitted, examples=examples)
            prose = await self._client.generate(
                system_instruction=system_prompt,
                messages=messages,
            )
            if profile is not None:
                prose = _without_personal_identity(prose)
            rendered = render_bid(prose, quote, lang)
            return _with_profile(rendered, profile, lang) if profile is not None else rendered
        except QuotaExceededError:
            # Keep quota/rate-limit separate so callers can show a precise notice.
            raise
        except Exception as exc:
            raise BidGenerationError(str(exc)) from exc

    def _build_messages(
        self,
        project: Project,
        lang: Language,
        *,
        vague: bool,
        examples: dict[str, Any],
    ) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for example in examples.get("examples", []):
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


def _without_personal_identity(text: str) -> str:
    """Discard identity lines, preserving project and technical reference URLs."""
    return "\n".join(
        line
        for line in text.splitlines()
        if not _LEGACY_IDENTITY_RE.search(line) and not _PERSONAL_IDENTITY_RE.search(line)
    ).strip()


def _with_profile(rendered: str, profile: dict[str, str], language: Language) -> str:
    signature = []
    if profile["name"]:
        signature.append(profile["name"])
    if profile["portfolio_url"]:
        label = "Портфолио" if language == "ru" else "Портфоліо"
        signature.append(f"{label}: {profile['portfolio_url']}")
    if not signature:
        return rendered
    # The pricing renderer normalizes dashes. Insert the profile afterwards so
    # URLs containing e.g. a double hyphen survive byte-for-byte unchanged.
    body, separator, closing = rendered.rpartition("\n\n")
    if not separator:
        return rendered + "\n\n" + "\n".join(signature)
    return f"{body}\n\n" + "\n".join(signature) + f"\n\n{closing}"


def detect_language(text: str) -> Language:
    if not text:
        return "ru"
    for ch in text:
        if ch in _UA_ONLY_LETTERS:
            return "ua"
    return "ru"
