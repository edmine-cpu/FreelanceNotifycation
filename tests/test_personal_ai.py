import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.ai.bid_generator import (
    EXAMPLES_FILE,
    SYSTEM_PROMPT_FILE,
    BidGenerator,
)
from app.ai.pricing import AtomicQuoteStore
from app.projects import Project
from app.llm import LLMResult
from app.ai.policy import AI_OWNER_ID


def _project() -> Project:
    return Project(
        id="shared-project",
        url="https://example.com/projects/shared",
        title="Telegram bot",
        budget="",
        description="Bot with a calendar integration",
        relative_time="",
        absolute_time="",
        published_ts=1,
    )


class _Estimator:
    async def estimate(self, project: Project) -> str:
        return "4"


class _Client:
    def __init__(self, response: str = "Здравствуйте\nПомогу связать бота с календарём") -> None:
        self.response = response
        self.calls: list[dict] = []
        self.block_first = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, *, system_instruction: str, messages: list, **kwargs) -> str:
        self.calls.append({"system": system_instruction, "messages": messages})
        if self.block_first and len(self.calls) == 1:
            self.started.set()
            await self.release.wait()
        return LLMResult(json.dumps({"prose": self.response}))


class PersonalBidGeneratorTest(unittest.IsolatedAsyncioTestCase):
    def _generator(self, directory: Path, client: _Client, profile: dict, marker: str) -> BidGenerator:
        directory.mkdir(parents=True)
        self._write_prompt(directory, marker)

        async def current_profile() -> dict[str, str]:
            return dict(profile)

        return BidGenerator(
            client, user_id=AI_OWNER_ID,
            system_prompt_path=directory / "system.md",
            examples_path=directory / "examples.json",
            profile_provider=current_profile,
            scope_estimator=_Estimator(),
            quote_store=AtomicQuoteStore(directory / "quotes.json"),
        )

    def _write_prompt(self, directory: Path, marker: str) -> None:
        (directory / "system.md").write_text(f"Стиль {marker}", encoding="utf-8")
        (directory / "examples.json").write_text(
            json.dumps(
                {"examples": [{"input": {"project_title": marker}, "output": f"Пример {marker}"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    async def test_users_have_distinct_prompts_examples_and_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _Client()
            profile_a = {"name": "Анна", "portfolio_url": "https://anna.example/work"}
            profile_b = {"name": "Богдан", "portfolio_url": "https://bohdan.example/work"}
            a = self._generator(Path(tmp) / "a", client, profile_a, "STYLE_A")
            b = self._generator(Path(tmp) / "b", client, profile_b, "STYLE_B")

            bids = await asyncio.gather(a.generate(_project()), b.generate(_project()))

            for index, (own, other, marker, foreign) in enumerate(
                [(profile_a, profile_b, "STYLE_A", "STYLE_B"), (profile_b, profile_a, "STYLE_B", "STYLE_A")]
            ):
                self.assertNotIn(own["name"], bids[index])
                self.assertIn(own["portfolio_url"], bids[index])
                self.assertNotIn(other["name"], bids[index])
                self.assertNotIn(other["portfolio_url"], bids[index])
                call = next(call for call in client.calls if marker in call["system"])
                payload = call["system"] + "\n".join(message.text for message in call["messages"])
                self.assertIn(own["name"], payload)
                self.assertIn(f"Пример {marker}", payload)
                self.assertNotIn(foreign, payload)
                self.assertNotIn(other["name"], payload)
                self.assertNotIn(other["portfolio_url"], payload)
            self.assertTrue((Path(tmp) / "a" / "quotes.json").exists())
            self.assertTrue((Path(tmp) / "b" / "quotes.json").exists())

    async def test_edits_reload_and_inflight_generation_keeps_its_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "user"
            client = _Client()
            client.block_first = True
            profile = {"name": "Анна", "portfolio_url": "https://old.example/work"}
            generator = self._generator(directory, client, profile, "BEFORE")
            pending = asyncio.create_task(generator.generate(_project()))
            await asyncio.wait_for(client.started.wait(), timeout=2)
            try:
                profile.update(name="Ирина", portfolio_url="https://new.example/work")
                self._write_prompt(directory, "AFTER")
                second = await generator.generate(_project())
            finally:
                client.release.set()
            first = await pending

            self.assertNotIn("Анна", first)
            self.assertIn("https://old.example/work", first)
            self.assertNotIn("Ирина", first)
            self.assertNotIn("Ирина", second)
            self.assertIn("https://new.example/work", second)
            self.assertNotIn("Анна", second)
            self.assertIn("BEFORE", client.calls[0]["system"])
            self.assertNotIn("AFTER", client.calls[0]["system"])
            self.assertIn("AFTER", client.calls[1]["system"])
            self.assertNotIn("BEFORE", client.calls[1]["system"])
            self.assertIn("Пример BEFORE", client.calls[0]["messages"][1].text)
            self.assertIn("Пример AFTER", client.calls[1]["messages"][1].text)

    async def test_empty_profile_does_not_inherit_legacy_identity(self) -> None:
        client = _Client(
            "Привет, меня зовут Никита\n"
            "Свяжу бота с календарём\n"
            "Портфолио: https://freelancehunt.com/freelancer/edmine.html#portfolio\n"
            "Мене звати Микита"
        )

        async def profile() -> dict[str, str]:
            return {"name": "", "portfolio_url": ""}

        generator = BidGenerator(
            client, user_id=AI_OWNER_ID,
            system_prompt_path=SYSTEM_PROMPT_FILE,
            examples_path=EXAMPLES_FILE,
            profile_provider=profile,
            scope_estimator=_Estimator(),
        )

        bid = await generator.generate(_project())

        call = client.calls[0]
        payload = call["system"] + "\n".join(message.text for message in call["messages"])
        for leaked in ("Никита", "Микита", "edmine"):
            self.assertNotIn(leaked, bid)
            # Personal instructions/examples stay intact; output identity is code-owned.
        self.assertNotIn("Портфолио:", bid)
        self.assertIn("Свяжу бота с календарём", bid)

    async def test_exact_profile_url_and_ukrainian_label_preserve_quote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            portfolio = "https://portfolio.example/my--work?category=ui--ux#cases"
            profile = {"name": "Олена", "portfolio_url": portfolio}
            client = _Client(
                "Вітаю\n"
                "Підключу календар через https://docs.example.com/reference\n"
                "PORTFOLIO: https://outdated.example/old\n"
                "МЕНЕ ЗВАТИ Ірина\n"
                "Портфоліо: https://outdated.example/work"
            )
            generator = self._generator(Path(tmp) / "user", client, profile, "UA_STYLE")

            bid = await generator.generate(_project(), language="ua")

            self.assertNotIn("Олена", bid)
            self.assertIn(f"Портфоліо: {portfolio}", bid)
            self.assertEqual(bid.count(portfolio), 1)
            self.assertIn("https://docs.example.com/reference", bid)
            self.assertNotIn("outdated.example", bid)
            self.assertNotIn("Ірина", bid)
            self.assertTrue(bid.splitlines()[-1].startswith("Орієнтовні ціна, строки:"))

    async def test_default_constructor_uses_generic_prompt_and_no_owner_examples(self) -> None:
        client = _Client()
        generator = BidGenerator(client, user_id=AI_OWNER_ID, scope_estimator=_Estimator())

        await generator.generate(_project())

        call = client.calls[0]
        for leaked in ("Никита", "Микита", "edmine"):
            self.assertNotIn(leaked, call["system"])
        self.assertEqual(len(call["messages"]), 1)

    async def test_portfolio_project_content_and_style_instructions_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "user"
            client = _Client("Здравствуйте\nСделаю сайт-портфолио для ваших работ")
            generator = self._generator(directory, client, {}, "PERSONAL_STYLE")
            instruction = "Сделай текст коротким и добавь портфолио"
            (directory / "system.md").write_text(instruction, encoding="utf-8")
            (directory / "examples.json").write_text(
                json.dumps({"examples": [{"input": {}, "output": "Создам сайт-портфолио"}]}),
                encoding="utf-8",
            )

            bid = await generator.generate(_project())

            self.assertIn(instruction, client.calls[0]["system"])
            self.assertIn("Создам сайт-портфолио", client.calls[0]["messages"][1].text)
            self.assertIn("Сделаю сайт-портфолио для ваших работ", bid)


if __name__ == "__main__":
    unittest.main()
