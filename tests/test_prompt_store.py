import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai import prompt_store
from app.ai.prompt_store import (
    PromptJsonError,
    canonical_prompt_hash,
    ensure_prompt_json,
    read_prompt_json,
    sanitize_prompt_data,
    write_prompt_json,
)


def _style_data(output: str = "Привет, готов выполнить задачу") -> dict:
    return {
        "examples": [
            {
                "input": {
                    "project_title": "Парсер цен",
                    "project_description": "Следить за ценой товара раз в час",
                    "language": "ru",
                },
                "output": output,
            }
        ]
    }


class PromptSanitizationTest(unittest.TestCase):
    def test_sanitizer_removes_pricing_inputs_and_output_lines_in_memory(self) -> None:
        source = _style_data(
            "Живой — текст\nЦена: 100 USD, срок 2 дня\nПишите -- обсудим"
        )
        source["examples"][0]["input"].update(
            {"budget": "100 USD", "hours": 8, "deadline": "1-2"}
        )
        original = copy.deepcopy(source)

        clean = sanitize_prompt_data(source)

        self.assertEqual(source, original)
        self.assertNotIn("budget", clean["examples"][0]["input"])
        self.assertNotIn("hours", clean["examples"][0]["input"])
        self.assertNotIn("deadline", clean["examples"][0]["input"])
        self.assertEqual(clean["examples"][0]["output"], "Живой - текст\nПишите - обсудим")

    def test_stock_examples_are_style_only(self) -> None:
        data = json.loads(prompt_store.EXAMPLES_FILE.read_text(encoding="utf-8"))

        self.assertEqual(sanitize_prompt_data(data), data)

    def test_upload_with_pricing_is_rejected_without_overwriting_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "examples.json"
            existing = _style_data()
            path.write_text(json.dumps(existing), encoding="utf-8")
            upload = _style_data("Сделаю за 100 USD\nПишите, обсудим")

            with self.assertRaises(PromptJsonError):
                write_prompt_json(json.dumps(upload), path)

            self.assertEqual(json.loads(path.read_text()), existing)

    def test_upload_with_pricing_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "examples.json"
            upload = _style_data()
            upload["examples"][0]["input"]["budget"] = "5000 UAH"

            with self.assertRaises(PromptJsonError):
                write_prompt_json(json.dumps(upload), path)

            self.assertFalse(path.exists())

    def test_style_upload_normalizes_dashes_instead_of_rejecting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "examples.json"

            write_prompt_json(json.dumps(_style_data("Готов — пишите -- обсудим")), path)

            stored = json.loads(path.read_text())
            self.assertEqual(stored["examples"][0]["output"], "Готов - пишите - обсудим")

    def test_read_sanitizes_custom_file_without_changing_it_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "examples.json"
            custom = _style_data("Опыт есть\nСтоимость: 5000 грн, 2 дня")
            custom["examples"][0]["input"]["budget"] = "5000 UAH"
            raw = json.dumps(custom, ensure_ascii=False)
            path.write_text(raw, encoding="utf-8")

            visible = json.loads(read_prompt_json(path))

            self.assertEqual(path.read_text(encoding="utf-8"), raw)
            self.assertNotIn("budget", visible["examples"][0]["input"])
            self.assertEqual(visible["examples"][0]["output"], "Опыт есть")


class PromptMigrationTest(unittest.TestCase):
    def test_known_legacy_stock_hash_is_pinned(self) -> None:
        self.assertIn(
            "7a6c669e85fd02e46058824e27d6b18cd36ffa82daf9188cb556204012890b0c",
            prompt_store._LEGACY_STOCK_HASHES,
        )

    def test_exact_legacy_hash_is_migrated_to_clean_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "examples.json"
            default_path = Path(tmp) / "default.json"
            legacy = _style_data("Цена: 100 USD, 2 дня")
            clean_default = _style_data("Готов помочь - пишите")
            path.write_text(json.dumps(legacy), encoding="utf-8")
            default_path.write_text(json.dumps(clean_default), encoding="utf-8")
            legacy_hash = canonical_prompt_hash(legacy)

            with patch.object(prompt_store, "_LEGACY_STOCK_HASHES", {legacy_hash}):
                ensure_prompt_json(path, default_path)

            self.assertEqual(json.loads(path.read_text()), clean_default)

    def test_custom_existing_file_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "examples.json"
            default_path = Path(tmp) / "default.json"
            custom = _style_data("Мой уникальный стиль\nЦена: 777 USD")
            raw = json.dumps(custom, ensure_ascii=False, indent=1)
            path.write_text(raw, encoding="utf-8")
            default_path.write_text(json.dumps(_style_data()), encoding="utf-8")

            ensure_prompt_json(path, default_path)

            self.assertEqual(path.read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
