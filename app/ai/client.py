"""Direct Anthropic Messages API, with mandatory access and usage accounting."""
import asyncio
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections.abc import Awaitable, Callable

import httpx

from app.llm import AIRequest, ChatMessage, LLMError, LLMResponseError, LLMResult, QuotaExceededError, UncertainRequestError
from .policy import require_ai_access
from .store import AIStore


class AnthropicClient:
    def __init__(self, api_key: str, model: str, *, http: httpx.AsyncClient,
                 store: AIStore, timeout: float = 120, max_tokens: int = 2048,
                 effort: str | None = None, on_usage: Callable[[], Awaitable[None]] | None = None):
        self.model = model
        self._http, self.store = http, store
        self.timeout, self.max_tokens, self.effort = timeout, max_tokens, effort
        self._headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        self._on_usage = on_usage
        self._blocked_until = 0.0

    @property
    def cache_identity(self):
        return [self.model, self.max_tokens, self.effort, "messages-v1"]

    async def generate(self, *, system_instruction: str, messages: list[ChatMessage],
                       context: AIRequest | None = None, schema: dict | None = None) -> LLMResult:
        require_ai_access(context.user_id if context else None)
        if time.time() < self._blocked_until:
            raise LLMError("Anthropic временно недоступен. Попробуйте позже.")
        payload = {"model": self.model, "max_tokens": self.max_tokens,
                   "system": system_instruction,
                   "messages": [{"role": "assistant" if m.role == "model" else m.role,
                                 "content": m.text} for m in messages]}
        output = {}
        if self.effort:
            output["effort"] = self.effort
        if schema:
            output["format"] = {"type": "json_schema", "schema": schema}
        if output:
            payload["output_config"] = output
        # Opus 5.5 has always-on adaptive thinking. No temperature/thinking
        # override; Haiku defaults to thinking disabled. No SDK retry layer.
        for attempt_number in range(2):
            require_ai_access(context.user_id)
            attempt = self.store.start_attempt(context, self.model)
            try:
                response = await self._http.post("https://api.anthropic.com/v1/messages",
                                                 headers=self._headers, json=payload, timeout=self.timeout)
            except httpx.TransportError as exc:
                # A read/write timeout may follow a charge. Never auto-retry it.
                self.store.record(attempt, status="uncertain", model=self.model)
                self._blocked_until = time.time()+60
                raise UncertainRequestError("Связь с AI прервалась; результат и стоимость неизвестны. Повтор возможен только по новой кнопке.") from None
            request_id = response.headers.get("request-id", "")
            if response.status_code != 200:
                status = response.status_code
                self.store.record(attempt, status=f"http_{status}", model=self.model,
                                  request_id=request_id, rejected=400 <= status < 500)
                delay = retry_after(response)
                if status in {429, 500, 502, 503, 504, 529} and attempt_number == 0 and delay <= 30:
                    await asyncio.sleep(delay)
                    continue
                self._blocked_until = time.time() + (max(60, delay) if status in {429, 500, 502, 503, 504, 529} else 300)
                if status == 429:
                    raise QuotaExceededError("Anthropic ограничил частоту запросов. Попробуйте позже.", retry_after=delay)
                # Never log the response body: it can echo prompt or secret data.
                raise LLMError(f"Anthropic HTTP {status}. Проверьте ключ, баланс и настройки модели.")
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError()
            except ValueError:
                self.store.record(attempt, status="invalid_response", model=self.model, request_id=request_id)
                raise LLMResponseError("AI вернул некорректный ответ; стоимость неизвестна.") from None
            model = data.get("model") or self.model
            self.store.record(attempt, status="success", model=model, request_id=request_id,
                              usage=data.get("usage"))
            if self._on_usage:
                await self._on_usage()
            blocks = data.get("content")
            text = "".join(b.get("text", "") for b in (blocks if isinstance(blocks, list) else [])
                           if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)).strip()
            stop = data.get("stop_reason", "")
            if stop != "end_turn" or not text:
                self.store.validation_failed(context.operation_id)
                raise LLMResponseError("AI не вернул завершённый текст (отказ, пустой ответ или предел вывода).")
            return LLMResult(text, model, request_id, stop, data.get("usage") or {})
        raise AssertionError("unreachable")


def retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after", "2")
    try:
        return max(0, float(raw))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return 2
