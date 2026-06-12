import asyncio
import logging

import httpx

from app.llm import ChatMessage, LLMError, LLMResponseError, QuotaExceededError

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
                data = _response_json(response)
                text = _extract_text(data)
                if not text:
                    raise LLMResponseError(_empty_response_message(data))
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
                    raise LLMError(_http_error_message(exc.response)) from exc
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
                    raise LLMError(
                        "Gemini request failed after "
                        f"{attempt + 1} attempts: {type(exc).__name__}: {exc}"
                    ) from exc
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


def _response_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError as exc:
        raise LLMResponseError(
            "Gemini returned non-JSON response: "
            f"{_shorten(response.text, limit=500)}"
        ) from exc


def _empty_response_message(data: object) -> str:
    details = _empty_response_details(data)
    if details:
        return f"Gemini returned empty text response ({details})"
    return "Gemini returned empty text response"


def _empty_response_details(data: object) -> str:
    if not isinstance(data, dict):
        return f"response_type={type(data).__name__}"

    details: list[str] = []
    prompt_feedback = data.get("promptFeedback")
    if isinstance(prompt_feedback, dict):
        block_reason = prompt_feedback.get("blockReason")
        if block_reason:
            details.append(f"prompt_block_reason={block_reason}")
        prompt_safety = _format_safety(prompt_feedback.get("safetyRatings"))
        if prompt_safety:
            details.append(f"prompt_safety={prompt_safety}")

    candidates = data.get("candidates")
    if isinstance(candidates, list):
        details.append(f"candidates={len(candidates)}")
        if candidates:
            candidate = candidates[0]
            if isinstance(candidate, dict):
                finish_reason = candidate.get("finishReason")
                if finish_reason:
                    details.append(f"finish_reason={finish_reason}")
                safety = _format_safety(candidate.get("safetyRatings"))
                if safety:
                    details.append(f"safety={safety}")
                content = candidate.get("content")
                if content is None:
                    details.append("candidate_has_no_content")
                elif isinstance(content, dict):
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        details.append(f"parts={len(parts)}")
    else:
        details.append("candidates=missing")

    return ", ".join(details)


def _format_safety(raw: object) -> str:
    if not isinstance(raw, list):
        return ""

    parts: list[str] = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        probability = item.get("probability")
        blocked = item.get("blocked")
        if category and probability:
            suffix = ",blocked" if blocked is True else ""
            parts.append(f"{category}:{probability}{suffix}")
    return ";".join(parts)


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


def _http_error_message(response: httpx.Response) -> str:
    return f"Gemini HTTP {response.status_code}: {_shorten(_error_message(response))}"


def _shorten(text: str, *, limit: int = 1000) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."
