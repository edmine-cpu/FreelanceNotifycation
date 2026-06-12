import unittest

from app.ai.client import _empty_response_message, _extract_text


class GeminiClientTest(unittest.TestCase):
    def test_extract_text_joins_all_response_parts(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "first"},
                            {"text": " second"},
                        ]
                    }
                }
            ]
        }

        self.assertEqual(_extract_text(payload), "first second")

    def test_extract_text_returns_empty_string_for_malformed_response(self) -> None:
        self.assertEqual(_extract_text({"candidates": []}), "")

    def test_empty_response_message_includes_finish_reason(self) -> None:
        payload = {
            "candidates": [
                {
                    "finishReason": "SAFETY",
                    "safetyRatings": [
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "probability": "HIGH",
                            "blocked": True,
                        }
                    ],
                }
            ]
        }

        message = _empty_response_message(payload)

        self.assertIn("finish_reason=SAFETY", message)
        self.assertIn("HARM_CATEGORY_DANGEROUS_CONTENT:HIGH,blocked", message)
        self.assertIn("candidate_has_no_content", message)

    def test_empty_response_message_includes_prompt_block_reason(self) -> None:
        payload = {
            "promptFeedback": {"blockReason": "BLOCKLIST"},
            "candidates": [],
        }

        message = _empty_response_message(payload)

        self.assertIn("prompt_block_reason=BLOCKLIST", message)
        self.assertIn("candidates=0", message)
