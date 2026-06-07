import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.projects import Project
from app.storage import StateStore


class CategorySettingsTest(unittest.TestCase):
    def test_custom_name_overrides_unknown_category(self) -> None:
        settings = Settings(
            telegram_bot_token="token",
            telegram_chat_id="1",
            freelancehunt_token="token",
            skill_ids="28,180",
            category_names={28: "Дизайн"},
        )

        categories = settings.categories

        self.assertEqual(categories[0].name, "Дизайн")
        self.assertEqual(categories[1].name, "Разработка ботов")


class CategoryStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_category_name_persists_and_relabels_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            await store.add_projects(
                [
                    Project(
                        id="project-1",
                        url="https://example.com/project-1",
                        title="Project",
                        budget="",
                        description="",
                        relative_time="",
                        absolute_time="",
                        published_ts=10,
                        skill_id=28,
                        category_name="Категория #28",
                        category_url="https://example.com/categories/28",
                    )
                ]
            )

            await store.set_category_name(28, "CRM / ERP")

            self.assertEqual(await store.category_names(), {28: "CRM / ERP"})
            self.assertEqual((await store.recent_projects())[0].category_name, "CRM / ERP")

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["category_names"]["28"], "CRM / ERP")

            reloaded = StateStore(path)
            self.assertEqual(await reloaded.category_names(), {28: "CRM / ERP"})
            self.assertEqual((await reloaded.recent_projects())[0].category_name, "CRM / ERP")

    async def test_category_names_merge_runtime_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            await store.set_category_name(28, "Mobile Apps")

            names = await store.category_names({180: "Bots"})

            self.assertEqual(names, {180: "Bots", 28: "Mobile Apps"})

    async def test_stale_project_fetch_does_not_reset_custom_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            await store.set_category_name(28, "Support")

            await store.add_projects(
                [
                    Project(
                        id="project-1",
                        url="https://example.com/project-1",
                        title="Project",
                        budget="",
                        description="",
                        relative_time="",
                        absolute_time="",
                        published_ts=10,
                        skill_id=28,
                        category_name="Категория #28",
                        category_url="https://example.com/categories/28",
                    )
                ]
            )

            self.assertEqual((await store.recent_projects())[0].category_name, "Support")
