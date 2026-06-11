import asyncio
import logging

import httpx

from app.llm import ChatMessage, QuotaExceededError

log = logging.getLogger(__name__)

_API_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class GeminiClient:
    """Gemini implementation of ``app.llm.LLMClient``. Talks to the REST API
    over httpx directly, so no vendor SDK dependency is needed."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        max_retries: int = 4,
        retry_base_delay: float = 2.0,
    ) -> None:
        model_path = model.removeprefix("models/")
        self._api_url = _API_URL_TEMPLATE.format(model=model_path)
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._headers = {
            "x-goog-api-key": api_key,
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
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [
                {"role": m.role, "parts": [{"text": m.text}]}
                for m in messages
            ],
            "generationConfig": {"temperature": temperature},
        }
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._api_url, headers=self._headers, json=payload
                    )
                response.raise_for_status()
                text = _extract_text(response.json())
                if not text:
                    raise RuntimeError("Gemini returned empty response")
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
                # On 429, Gemini may tell us exactly how long to wait via Retry-After.
                delay = (
                    retry_after
                    if retry_after is not None
                    else self._retry_base_delay * (2 ** attempt)
                )
                log.warning(
                    "gemini transient error %s, retrying in %.1fs (attempt %d/%d)",
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
                    "gemini request error (%s), retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__, delay, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc


def _extract_text(data: object) -> str:
    """Pull text parts out of a Gemini generateContent response."""
    try:
        parts = data["candidates"][0]["content"]["parts"]  # type: ignore[index]
        texts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text", "")
            if isinstance(text, str):
                texts.append(text)
        return "".join(texts).strip()
    except (AttributeError, KeyError, IndexError, TypeError):
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
