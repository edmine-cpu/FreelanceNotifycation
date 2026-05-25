import asyncio
import logging
import re

from google import genai
from google.genai import errors, types

from app.llm import ChatMessage, QuotaExceededError

log = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# Pulls the server's suggested wait out of the 429 body, e.g. 'retryDelay': '54s'.
_RETRY_DELAY_RE = re.compile(r"['\"]retryDelay['\"]\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def _is_daily_quota_exhausted(exc: errors.APIError) -> bool:
    """A free-tier *daily* quota (PerDay) won't reset for hours — retrying is futile."""
    return exc.code == 429 and "PerDay" in str(exc)


def _retry_after_seconds(exc: errors.APIError) -> float | None:
    match = _RETRY_DELAY_RE.search(str(exc))
    return float(match.group(1)) if match else None


def _to_content(message: ChatMessage) -> types.Content:
    """Translate a neutral ChatMessage into the genai SDK's Content type."""
    return types.Content(role=message.role, parts=[types.Part.from_text(text=message.text)])


class GeminiClient:
    """Gemini implementation of ``app.llm.LLMClient`` — the one place that knows
    about the google-genai SDK."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        max_retries: int = 4,
        retry_base_delay: float = 2.0,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._client = genai.Client(api_key=api_key)

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[ChatMessage],
        temperature: float = 0.8,
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
        )
        contents = [_to_content(m) for m in messages]
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self._model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=self._timeout,
                )
                text = (response.text or "").strip()
                if not text:
                    raise RuntimeError("Gemini returned empty response")
                return text
            except errors.APIError as exc:
                last_exc = exc
                give_up = (
                    exc.code not in _RETRYABLE_STATUSES
                    or attempt == self._max_retries
                    or _is_daily_quota_exhausted(exc)
                )
                if give_up:
                    if exc.code == 429:
                        raise QuotaExceededError(
                            str(exc), retry_after=_retry_after_seconds(exc)
                        ) from exc
                    raise
                delay = self._retry_base_delay * (2 ** attempt)
                log.warning(
                    "gemini transient error %s, retrying in %.1fs (attempt %d/%d)",
                    exc.code, delay, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(delay)
            except asyncio.TimeoutError as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    raise
                delay = self._retry_base_delay * (2 ** attempt)
                log.warning(
                    "gemini timeout, retrying in %.1fs (attempt %d/%d)",
                    delay, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc
