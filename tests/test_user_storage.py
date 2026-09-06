import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, User

from app.config import Settings
from app.projects import Project
from app.storage import StateStore
from app.storage.users import UserRegistry
from app.telegram.user_context import UserContextMiddleware


def _project(skill_id: int = 180) -> Project:
    return Project(
        id="project-1", url="https://example.test/project-1", title="Project",
        budget="", description="", relative_time="", absolute_time="",
        published_ts=10, skill_id=skill_id, category_name="Bots", category_url="",
    )


def _message(user_id: int, *, chat_id: int | None = None, chat_type: str = "private") -> Message:
    return Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id if chat_id is not None else user_id, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="User"), text="/start",
    )


class UserRegistryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        public_prompt = self.path / "public.md"
        public_prompt.write_text("Generic user prompt", encoding="utf-8")
        legacy_prompt = self.path / "legacy.md"
        legacy_prompt.write_text("Legacy owner prompt", encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch("app.storage.users.PUBLIC_SYSTEM_PROMPT", public_prompt).start()
        patch("app.storage.users.LEGACY_SYSTEM_PROMPT", legacy_prompt).start()
        self.settings = Settings(
            _env_file=None, telegram_bot_token="token", telegram_chat_id="1",
            freelancehunt_token="token", skill_ids="180,99",
            state_file=self.path / "data" / "state.json",
            quote_file=self.path / "data" / "quotes.json",
        )

    async def test_user_settings_state_and_templates_are_isolated(self) -> None:
        registry = UserRegistry(self.settings)
        alice, bob = await registry.get(10), await registry.get(20)
        await alice.store.remove_skill_id(99, [180, 99])
        await alice.store.add_skill_id(28, [180, 99])
        await alice.store.set_category_name(180, "Alice's bots")
        await alice.store.set_profile("Alice", "https://example.test/alice")
        await alice.store.toggle_muted_skill_id(180)
        await alice.store.mark_seen(["project-1"])
        await alice.store.mark_passed(["project-1"])
        await alice.store.update_last_published_ts(180, 10)
        await alice.store.add_projects([_project()])
        alice.prompt_examples_path.write_text('{"examples": [] , "owner": "alice"}', encoding="utf-8")
        alice.system_prompt_path.write_text("Alice prompt", encoding="utf-8")
        alice.quote_path.write_text('{"quotes": {"project-1": {"price": 123}}}', encoding="utf-8")

        alice_settings, bob_settings = await alice.settings(), await bob.settings()
        self.assertEqual(alice_settings.telegram_chat_id, "10")
        self.assertEqual(bob_settings.telegram_chat_id, "20")
        self.assertEqual(alice_settings.skill_ids, "180,28")
        self.assertEqual(bob_settings.skill_ids, "180,99")
        self.assertEqual(alice_settings.category_names, {180: "Alice's bots"})
        self.assertEqual(bob_settings.category_names, {})
        self.assertEqual(await bob.store.profile(), {"name": "", "portfolio_url": ""})
        self.assertEqual(await bob.store.muted_skill_ids(), set())
        self.assertFalse(await bob.store.is_seen("project-1"))
        self.assertEqual(await bob.store.passed_ids(), set())
        self.assertFalse(bob.store.has_watermark(180))
        self.assertEqual(await bob.store.recent_projects(), [])
        self.assertIsNone(await bob.store.find_project("project-1"))
        self.assertEqual(json.loads(bob.prompt_examples_path.read_text()), {"examples": []})
        self.assertEqual(bob.system_prompt_path.read_text(), "Generic user prompt")
        self.assertEqual(json.loads(bob.quote_path.read_text()), {"quotes": {}})
        self.assertFalse(bob.is_legacy_owner)
        self.assertEqual(self.settings.skill_ids, "180,99")
        self.assertEqual(self.settings.category_names, {})
        self.assertEqual(self.settings.telegram_chat_id, "1")

    async def test_defaults_are_copied_from_original_settings(self) -> None:
        registry = UserRegistry(self.settings)
        self.settings.skill_ids = "777"
        self.settings.category_names[180] = "Owner changed settings"
        user = await registry.get(20)
        snapshot = await user.settings()
        self.assertEqual(snapshot.skill_ids, "180,99")
        self.assertEqual(snapshot.category_names, {})
        snapshot.category_names[180] = "Request mutation"
        self.assertEqual((await user.settings()).category_names, {})

    async def test_registration_and_empty_categories_survive_restart(self) -> None:
        registry = UserRegistry(self.settings)
        user = await registry.get(20)
        await user.store.set_profile(name="Bob", portfolio_url="https://example.test/bob")
        await user.store.remove_skill_id(180, [180, 99])
        await user.store.remove_skill_id(99, [180, 99])
        await user.store.set_active(False)
        user.system_prompt_path.write_text("Bob's saved prompt", encoding="utf-8")

        restarted = UserRegistry(self.settings)
        users = await restarted.users()
        self.assertEqual([context.user_id for context in users], [20])
        restored = await restarted.get(20)
        self.assertIs(restored, users[0])
        self.assertEqual((await restored.settings()).categories, [])
        self.assertEqual(await restored.store.skill_ids([180, 99]), [])
        self.assertEqual(await restored.store.profile(), {"name": "Bob", "portfolio_url": "https://example.test/bob"})
        self.assertFalse(await restored.store.is_active())
        self.assertEqual(restored.system_prompt_path.read_text(), "Bob's saved prompt")

    async def test_migration_imports_only_owner_and_preserves_originals(self) -> None:
        self.settings.state_file.parent.mkdir()
        legacy_state = StateStore(self.settings.state_file)
        await legacy_state.add_skill_id(28, [180])
        await legacy_state.set_category_name(28, "Owner category")
        await legacy_state.mark_seen(["old-project"])
        examples = self.settings.state_file.parent / "bids_examples.json"
        examples.write_text('{"examples": [{"input": {}, "output": "Owner sample"}]}', encoding="utf-8")
        self.settings.quote_file.write_text('{"quotes": {"old-project": {"price": 100}}}', encoding="utf-8")
        originals = {path: path.read_bytes() for path in (self.settings.state_file, examples, self.settings.quote_file)}

        registry = UserRegistry(self.settings)
        await registry.migrate_legacy()
        owner, newcomer = await registry.get(1), await registry.get(2)
        self.assertTrue(owner.is_legacy_owner)
        self.assertTrue((await owner.settings()).primary_filter_enabled)
        self.assertFalse((await newcomer.settings()).primary_filter_enabled)
        self.assertEqual((await owner.settings()).skill_ids, "180,28")
        self.assertEqual((await owner.settings()).category_names, {28: "Owner category"})
        self.assertTrue(await owner.store.is_seen("old-project"))
        self.assertEqual(await owner.store.profile(), {
            "name": "Никита", "portfolio_url": "https://freelancehunt.com/freelancer/edmine.html#portfolio",
        })
        self.assertEqual(owner.system_prompt_path.read_text(), "Legacy owner prompt")
        self.assertEqual(owner.prompt_examples_path.read_bytes(), originals[examples])
        self.assertEqual(owner.quote_path.read_bytes(), originals[self.settings.quote_file])
        self.assertEqual((await newcomer.settings()).skill_ids, "180,99")
        self.assertEqual((await newcomer.settings()).category_names, {})
        self.assertFalse(await newcomer.store.is_seen("old-project"))
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

        await owner.store.set_profile(name="Changed owner")
        owner.system_prompt_path.write_text("Customized owner prompt", encoding="utf-8")
        restarted = UserRegistry(self.settings)
        await restarted.migrate_legacy()
        restored = await restarted.get(1)
        self.assertTrue(restored.is_legacy_owner)
        self.assertEqual((await restored.store.profile())["name"], "Changed owner")
        self.assertEqual(restored.system_prompt_path.read_text(), "Customized owner prompt")

    async def test_migration_uses_configured_examples_path(self) -> None:
        self.settings.prompt_examples_file = self.path / "custom-examples.json"
        self.settings.prompt_examples_file.write_text('{"examples": [], "custom": true}', encoding="utf-8")
        registry = UserRegistry(self.settings)
        owner = await registry.get(1)
        self.assertTrue(owner.is_legacy_owner)
        self.assertEqual(owner.prompt_examples_path.read_bytes(), self.settings.prompt_examples_file.read_bytes())

    async def test_existing_user_is_never_overwritten_by_migration(self) -> None:
        registry = UserRegistry(self.settings)
        owner = await registry.get(1)
        await owner.store.set_profile(name="Already registered")
        self.settings.state_file.write_text('{"skill_ids": [777]}', encoding="utf-8")
        restarted = UserRegistry(self.settings)
        await restarted.migrate_legacy()
        owner = await restarted.get(1)
        self.assertFalse(owner.is_legacy_owner)
        self.assertEqual((await owner.store.profile())["name"], "Already registered")
        self.assertEqual((await owner.settings()).skill_ids, "180,99")

    async def test_non_user_legacy_chat_does_not_import_private_data(self) -> None:
        self.settings.telegram_chat_id = "-1001234"
        self.settings.state_file.parent.mkdir()
        self.settings.state_file.write_text('{"skill_ids": [777]}', encoding="utf-8")
        registry = UserRegistry(self.settings)
        self.assertEqual(await registry.users(), [])
        newcomer = await registry.get(2)
        self.assertFalse(newcomer.is_legacy_owner)
        self.assertEqual((await newcomer.settings()).skill_ids, "180,99")
        for invalid in (0, -1, True, "2"):
            with self.assertRaises(ValueError):
                await registry.get(invalid)

    async def test_removing_category_hides_history_and_stale_callbacks(self) -> None:
        user = await UserRegistry(self.settings).get(20)
        await user.store.add_projects([_project()])
        await user.store.toggle_muted_skill_id(180)
        await user.store.set_category_name(180, "Renamed")
        await user.store.update_last_published_ts(180, 10)
        self.assertTrue(await user.store.remove_skill_id(180, [180, 99]))
        # A fetch already in progress must not restore access to the category.
        await user.store.add_projects([_project()])
        self.assertEqual(await user.store.recent_projects(), [])
        self.assertIsNone(await user.store.find_project("project-1"))
        self.assertFalse(user.store.has_watermark(180))
        self.assertEqual(await user.store.muted_skill_ids(), set())
        self.assertEqual(await user.store.category_names(), {})
        self.assertFalse(await user.store.remove_skill_id(180, [180, 99]))

    async def test_private_middleware_injects_user_without_resuming_notifications(self) -> None:
        registry = UserRegistry(self.settings)
        user = await registry.get(20)
        await user.store.set_active(False)
        generator = object()
        factory = AsyncMock(return_value=generator)
        middleware = UserContextMiddleware(registry, factory)
        handler = AsyncMock(return_value="handled")
        shared_source = object()
        data = {"source": shared_source, "settings": self.settings}
        message = _message(20)

        result = await middleware(handler, message, data)

        self.assertEqual(result, "handled")
        self.assertFalse(await user.store.is_active())
        factory.assert_awaited_once_with(user)
        handler.assert_awaited_once_with(message, data)
        self.assertIs(data["user_context"], user)
        self.assertIs(data["store"], user.store)
        self.assertIs(data["bid_generator"], generator)
        self.assertIs(data["source"], shared_source)
        self.assertEqual(data["settings"].telegram_chat_id, "20")
        self.assertEqual(data["prompt_examples_path"], user.prompt_examples_path)
        self.assertEqual(data["system_prompt_path"], user.system_prompt_path)

    async def test_middleware_blocks_groups_mismatched_senders_and_inline_callbacks(self) -> None:
        registry = UserRegistry(self.settings)
        middleware = UserContextMiddleware(registry, lambda user: None)
        handler = AsyncMock()
        messages = [_message(20, chat_id=-100123, chat_type="group"), _message(20, chat_id=21)]
        for message in messages:
            await middleware(handler, message, {})
        callbacks = [
            CallbackQuery(id="group", from_user=messages[0].from_user, chat_instance="a", message=messages[0]),
            CallbackQuery(id="mismatch", from_user=messages[1].from_user, chat_instance="b", message=messages[1]),
            CallbackQuery(id="inline", from_user=messages[0].from_user, chat_instance="c", inline_message_id="inline"),
        ]
        with patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer:
            for callback in callbacks:
                await middleware(handler, callback, {})
            self.assertEqual(answer.await_count, 3)
        handler.assert_not_awaited()
        self.assertEqual(await registry.users(), [])

    async def test_private_callback_uses_clicking_users_workspace(self) -> None:
        registry = UserRegistry(self.settings)
        middleware = UserContextMiddleware(registry, lambda user: None)
        handler = AsyncMock()
        message = _message(20)
        callback = CallbackQuery(id="private", from_user=message.from_user, chat_instance="a", message=message)
        data = {}
        await middleware(handler, callback, data)
        self.assertEqual(data["user_context"].user_id, 20)
        self.assertEqual(data["settings"].telegram_chat_id, "20")
        self.assertIsNone(data["bid_generator"])
        handler.assert_awaited_once_with(callback, data)

    async def test_inaccessible_callback_message_is_rejected_before_handlers(self) -> None:
        registry = UserRegistry(self.settings)
        middleware = UserContextMiddleware(registry, lambda user: None)
        handler = AsyncMock()
        callback = CallbackQuery(
            id="expired", from_user=User(id=20, is_bot=False, first_name="User"),
            chat_instance="a", message=InaccessibleMessage(message_id=1, chat=Chat(id=20, type="private")),
        )
        with patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer:
            await middleware(handler, callback, {})
            answer.assert_awaited_once()
            self.assertIn("/start", answer.await_args.args[0])
        handler.assert_not_awaited()
        self.assertEqual(await registry.users(), [])


if __name__ == "__main__":
    unittest.main()
