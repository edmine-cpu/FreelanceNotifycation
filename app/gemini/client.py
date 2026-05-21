import asyncio
import logging

from google import genai
from google.genai import errors, types

log = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class GeminiClient:
    """Thin async wrapper around the google-genai SDK."""

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
        system_instruction: str,
        contents: list[types.Content],
        temperature: float = 0.8,
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
        )
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
                if exc.code not in _RETRYABLE_STATUSES or attempt == self._max_retries:
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
