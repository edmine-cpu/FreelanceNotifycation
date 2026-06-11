import unittest

from app.ai.client import _extract_text


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

