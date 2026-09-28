import asyncio
import json
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx

from app.ai.client import AnthropicClient
from app.ai.policy import AI_OWNER_ID
from app.ai.store import AIStore, usage_cost
from app.llm import AIRequest, ChatMessage, LLMError, LLMResponseError, QuotaExceededError, UncertainRequestError


def response(text='{"prose":"Отклик"}', **changes):
    data = {"model": "claude-opus-5-5", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": "NEVER EXPOSE"}, {"type": "text", "text": text}],
            "usage": {"input_tokens": 100, "output_tokens": 50}}
    data.update(changes)
    return httpx.Response(200, json=data, headers={"request-id": "req-test"})


class AnthropicTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.responses = [response()]
        self.store = AIStore()
        self.addCleanup(self.store.close)
        def transport(request):
            self.calls.append(json.loads(request.content))
            value = self.responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.addAsyncCleanup(self.http.aclose)
        self.client = AnthropicClient("fake", "claude-opus-5-5", http=self.http, store=self.store, effort="low")
        self.context = AIRequest(AI_OWNER_ID, "42", "bid", "op")

    async def generate(self, **kwargs):
        return await self.client.generate(system_instruction="rules", messages=[ChatMessage("user", "sample"),
                ChatMessage("model", "example"), ChatMessage("user", "project")], **{"context": self.context, **kwargs})

    async def test_payload_and_text_usage_persisted_before_validation(self):
        result = await self.generate(schema={"type": "object"})
        self.assertEqual(result.request_id, "req-test")
        self.assertNotIn("NEVER EXPOSE", result.text)
        payload = self.calls[0]
        self.assertEqual(payload["messages"][1]["role"], "assistant")
        self.assertEqual(payload["system"], "rules")
        self.assertEqual(payload["output_config"]["effort"], "low")
        self.assertNotIn("temperature", payload)
        self.assertNotIn("thinking", payload)
        row = self.store.db.execute("SELECT * FROM usage").fetchone()
        self.assertEqual(Decimal(row["cost"]), Decimal("0.0014"))
        self.assertEqual(row["status"], "success")
        self.assertEqual(json.loads(row["tokens"])["output"], 50)

    async def test_missing_and_foreign_user_make_zero_transport_calls(self):
        for uid in [None, 1, "992784212", True]:
            with self.subTest(uid=uid), self.assertRaises(PermissionError):
                await self.generate(context=AIRequest(uid, "42", "bid", "x") if uid else None)
        self.assertEqual(self.calls, [])

    async def test_access_checked_again_on_retry(self):
        self.responses = [httpx.Response(503), response()]
        with patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock), patch("app.ai.client.require_ai_access", side_effect=[None, None, PermissionError("denied")]):
            with self.assertRaises(PermissionError):
                await self.generate()
        self.assertEqual(len(self.calls), 1)

    async def test_429_and_5xx_at_most_two_attempts_and_usage_not_doubled(self):
        for status in [429, 500, 529]:
            self.client._blocked_until = 0
            self.responses = [httpx.Response(status, headers={"retry-after": "0"}), response()]
            count = len(self.calls)
            with patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock):
                await self.generate()
            self.assertEqual(len(self.calls)-count, 2)
        rows = self.store.db.execute("SELECT * FROM usage").fetchall()
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum(Decimal(r["cost"]) for r in rows if r["cost"] is not None), Decimal("0.0042"))
        self.assertEqual(sum(r["cost"] is None for r in rows), 2)

    async def test_non_retryable_auth_billing_and_bad_parameters(self):
        for status in [400, 401, 402, 403, 404]:
            self.client._blocked_until = 0
            self.responses = [httpx.Response(status)]
            before = len(self.calls)
            with self.assertRaises(LLMError):
                await self.generate()
            with self.assertRaises(LLMError):
                await self.generate()
            self.assertEqual(len(self.calls)-before, 1)

    async def test_retry_after_and_exhausted_rate_limit(self):
        self.responses = [httpx.Response(429, headers={"retry-after": "12"}), httpx.Response(429)]
        with patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(QuotaExceededError):
                await self.generate()
            sleep.assert_awaited_once_with(12)

    async def test_timeout_is_unknown_and_never_automatically_retried(self):
        self.responses = [httpx.ReadTimeout("timeout")]
        with self.assertRaises(UncertainRequestError):
            await self.generate()
        self.assertEqual(len(self.calls), 1)
        row = self.store.db.execute("SELECT * FROM usage").fetchone()
        self.assertIsNone(row["cost"])
        self.assertEqual(row["status"], "uncertain")

    async def test_truncated_empty_refusal_still_billed(self):
        for text, stop in [("partial", "max_tokens"), ("", "end_turn"), ("no", "refusal")]:
            self.responses = [response(text, stop_reason=stop)]
            with self.assertRaises(LLMResponseError):
                await self.generate()
        rows = self.store.db.execute("SELECT * FROM usage").fetchall()
        self.assertTrue(all(r["status"] == "invalid_result" and r["cost"] for r in rows))

    async def test_non_json_success_has_unknown_cost(self):
        self.responses = [httpx.Response(200, text="broken")]
        with self.assertRaises(LLMResponseError):
            await self.generate()
        self.assertIsNone(self.store.db.execute("SELECT cost FROM usage").fetchone()[0])

    def test_cache_accounting_no_double_counting_or_thinking_surcharge(self):
        tokens, cost = usage_cost("claude-opus-5-5", {"input_tokens": 100, "output_tokens": 20,
            "cache_read_input_tokens": 200, "cache_creation_input_tokens": 150,
            "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 50},
            "thinking_tokens": 10})
        self.assertEqual(cost, Decimal("0.00174"))
        self.assertEqual(tokens["write5m"], 100)
        self.assertEqual(tokens["write1h"], 50)
        self.assertIsNone(usage_cost("unknown", {"input_tokens": 5, "output_tokens": 5})[1])

if __name__ == "__main__":
    unittest.main()
