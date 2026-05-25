import asyncio
import logging

import httpx

from app.llm import ChatMessage, QuotaExceededError

log = logging.getLogger(__name__)

_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# Our neutral ChatMessage uses "model" (Gemini's term) for assistant turns;
# Groq's OpenAI-compatible API calls that role "assistant".
_ROLE_MAP = {"user": "user", "model": "assistant"}


class GroqClient:
    """Groq implementation of ``app.llm.LLMClient`` — the one place that knows
    about Groq's OpenAI-compatible HTTP API. Talks to it over httpx directly, so
    no vendor SDK dependency is needed."""

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
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        *,
        system_instruction: str,
        messages: list[ChatMessage],
        temperature: float = 0.8,
    ) -> str:
        payload = {
            "model": self._model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_instruction},
                *(
                    {"role": _ROLE_MAP[m.role], "content": m.text}
                    for m in messages
                ),
            ],
        }
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        _API_URL, headers=self._headers, json=payload
                    )
                response.raise_for_status()
                text = _extract_text(response.json())
                if not text:
                    raise RuntimeError("Groq returned empty response")
                return text
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                retry_after = _retry_after_seconds(exc.response)
                give_up = status not in _RETRYABLE_STATUSES or attempt == self._max_retries
                if give_up:
                    if status == 429:
                        raise QuotaExceededError(
                            _error_message(exc.response), retry_after=retry_after
                        ) from exc
                    raise
                # On 429 Groq tells us exactly how long to wait via Retry-After.
                delay = (
                    retry_after
                    if retry_after is not None
                    else self._retry_base_delay * (2 ** attempt)
                )
                log.warning(
                    "groq transient error %s, retrying in %.1fs (attempt %d/%d)",
                    status, delay, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(delay)
            except httpx.TransportError as exc:
                # Covers timeouts and network errors; both are worth retrying.
                last_exc = exc
                if attempt == self._max_retries:
                    raise
                delay = self._retry_base_delay * (2 ** attempt)
                log.warning(
                    "groq request error (%s), retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__, delay, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc


def _extract_text(data: object) -> str:
    """Pull the assistant message out of an OpenAI-shaped chat completion."""
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return ""


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        return body.get("error", {}).get("message") or response.text
    except Exception:
        return response.text
