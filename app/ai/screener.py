import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.llm import ChatMessage, LLMClient, LLMError
from app.projects import Project

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SCREEN_PROMPT_FILE = PROMPTS_DIR / "screen_prompt.md"


@dataclass(frozen=True)
class ScreenResult:
    """Verdict of the primary check. ``stack`` is the model's short read of the
    required stack, kept only for logging."""

    allowed: bool
    stack: str = ""


class OrderScreener:
    """Primary check: a standalone AI pass that decides whether an order is worth
    notifying about, based on its required stack. Completely independent of bid
    generation (the secondary pass) — separate prompt, separate request.

    Allow when the stack is Python/JavaScript/TypeScript, or when no stack is
    specified. Skip when a different language/stack or a no-code platform
    (WordPress, Tilda, Bitrix…) is required.

    Fails open: any LLM or parse error yields ``allowed=True`` so a transient
    failure never silently swallows orders.
    """

    def __init__(
        self,
        client: LLMClient,
        system_prompt_path: Path = SCREEN_PROMPT_FILE,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()

    async def screen(self, project: Project) -> ScreenResult:
        payload = json.dumps(
            {"title": project.title, "description": project.description},
            ensure_ascii=False,
        )
        try:
            raw = await self._client.generate(
                system_instruction=self._system_prompt,
                messages=[ChatMessage(role="user", text=payload)],
                temperature=0.0,
            )
        except LLMError as exc:
            log.warning("primary check LLM error for %s, allowing through: %s", project.id, exc)
            return ScreenResult(allowed=True)
        except Exception:
            log.exception("primary check failed for %s, allowing through", project.id)
            return ScreenResult(allowed=True)
        return _parse_verdict(raw)


def _parse_verdict(raw: str) -> ScreenResult:
    text = raw.strip()
    # Models sometimes wrap the JSON in ```fences``` or add stray prose; grab the
    # outermost {...} object before parsing.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        decision = str(data.get("decision", "")).strip().lower()
        stack = str(data.get("stack", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        log.warning("primary check: unparseable verdict %r, allowing through", raw[:200])
        return ScreenResult(allowed=True)
    # Only an explicit "skip" filters the order out; anything else is fail-open.
    return ScreenResult(allowed=decision != "skip", stack=stack)
