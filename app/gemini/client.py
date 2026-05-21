import asyncio
import logging

from google import genai
from google.genai import types

log = logging.getLogger(__name__)


class GeminiClient:
    """Thin async wrapper around the google-genai SDK."""

    def __init__(self, api_key: str, model: str, timeout: float = 20.0) -> None:
        self._model = model
        self._timeout = timeout
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
