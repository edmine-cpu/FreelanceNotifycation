import tempfile
import unittest
from html import escape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from app.config import Settings
from app.notifier.loop import NotifierLoop
from app.notifier.public import PublicNotifier
from app.projects import Project
from app.storage.users import UserRegistry


def project(pid="42", skill=180, ts=20):
    return Project(
        id=pid, url=f"https://example.test/{pid}", title="New project",
        description="Build something", budget="", relative_time="", absolute_time="",
        published_ts=ts, skill_id=skill, category_name="Original",
    )


class PublicNotifierTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(
            _env_file=None, telegram_bot_token="test", freelancehunt_token="test",
            skill_ids="180", state_file=Path(self.temp.name) / "state.json",
        )
        self.registry = UserRegistry(self.settings)
        self.a = await self.registry.get(101)
        self.b = await self.registry.get(202)
        self.bot = AsyncMock()
        self.bot.send_message.return_value = SimpleNamespace(message_id=9)
        self.source = Mock()
        self.source.fetch_projects = AsyncMock(return_value=[project()])
        self.source.successful_skill_ids = {180}
        self.generators = {uid: Mock(generate=AsyncMock(return_value=f"Bid for {uid}")) for uid in (101, 202)}
        self.notifier = PublicNotifier(
            self.bot, self.registry, self.source, self.settings,
            lambda user: self.generators[user.user_id],
        )
        self.notifier._bot = self.bot
        self.sleep = patch("app.notifier.loop.asyncio.sleep", new_callable=AsyncMock)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    async def seed(self):
        for user in (self.a, self.b):
            await user.store.update_last_published_ts(180, 10)

    async def test_one_fetch_two_personal_deliveries_and_no_repeats_after_restart(self):
        await self.seed()
        await self.a.store.set_category_name(180, "A's categories")
        await self.b.store.set_category_name(180, "B's categories")
        await self.notifier._tick()
        self.source.fetch_projects.assert_awaited_once()
        calls = [call.kwargs for call in self.bot.send_message.await_args_list]
        self.assertEqual(len(calls), 4)
        self.assertEqual({c["chat_id"] for c in calls}, {"101", "202"})
        self.assertIn(escape("A's categories"), next(c["text"] for c in calls if c["chat_id"] == "101"))
        self.assertEqual(self.generators[101].generate.await_args.args[0].category_name, "A's categories")
        self.assertEqual(self.generators[202].generate.await_args.args[0].category_name, "B's categories")
        self.notifier._registry = UserRegistry(self.settings)
        await self.notifier._tick()
        self.assertEqual(self.bot.send_message.await_count, 4)

    async def test_muting_a_category_only_affects_its_user(self):
        await self.seed()
        await self.a.store.toggle_muted_skill_id(180)
        await self.notifier._tick()
        self.assertEqual({c.kwargs["chat_id"] for c in self.bot.send_message.await_args_list}, {"202"})
        self.assertTrue(await self.a.store.is_seen("42"))
        self.assertTrue(await self.b.store.is_seen("42"))

    async def test_muted_duplicate_does_not_hide_an_enabled_category(self):
        await self.seed()
        await self.a.store.add_skill_id(99, [180])
        await self.a.store.toggle_muted_skill_id(99)
        self.source.fetch_projects.return_value = [project(skill=99), project()]
        self.source.successful_skill_ids = {99, 180}
        await self.notifier._tick()
        self.generators[101].generate.assert_awaited_once()

    async def test_first_seen_backlog_suppressed_independently_for_new_user(self):
        await self.a.store.update_last_published_ts(180, 10)
        await self.notifier._tick()
        self.assertEqual({c.kwargs["chat_id"] for c in self.bot.send_message.await_args_list}, {"101"})
        self.assertEqual(self.b.store.last_published_ts(180), 20)

    async def test_empty_category_seeds_then_delivers_its_first_project(self):
        self.source.fetch_projects.return_value = []
        await self.notifier._tick()
        self.assertTrue(self.a.store.has_watermark(180))
        self.source.fetch_projects.return_value = [project()]
        await self.notifier._tick()
        self.assertEqual(self.bot.send_message.await_count, 4)

    async def test_failed_category_is_not_seeded(self):
        self.source.fetch_projects.return_value = []
        self.source.successful_skill_ids = set()
        await self.notifier._tick()
        self.assertFalse(self.a.store.has_watermark(180))

    async def test_blocked_user_does_not_prevent_other_recipient(self):
        await self.seed()

        async def send(**kwargs):
            if kwargs["chat_id"] == "101":
                raise TelegramForbiddenError(method=SendMessage(chat_id=101, text="x"), message="blocked")
            return SimpleNamespace(message_id=10)

        self.bot.send_message.side_effect = send
        await self.notifier._tick()
        self.assertFalse(await self.a.store.is_active())
        self.assertTrue(await self.b.store.is_seen("42"))
        self.assertEqual(self.a.store.last_published_ts(180), 10)

    async def test_disabled_users_and_removed_categories_not_polled(self):
        await self.a.store.set_active(False)
        await self.b.store.remove_skill_id(180, [180])
        await self.notifier._tick()
        self.source.fetch_projects.assert_not_awaited()

    async def test_muting_during_generation_stops_remaining_notifications_for_that_user(self):
        await self.seed()
        self.source.fetch_projects.return_value = [project("first", ts=20), project("second", ts=21)]

        async def generate(_project):
            await self.a.store.toggle_muted_skill_id(180)
            return "Already generated bid"

        self.generators[101].generate.side_effect = generate
        await self.notifier._tick()
        a_calls = [call.kwargs for call in self.bot.send_message.await_args_list if call.kwargs["chat_id"] == "101"]
        self.assertEqual(len(a_calls), 1)
        self.assertNotIn("reply_to_message_id", a_calls[0])
        self.generators[101].generate.assert_awaited_once()
        self.assertEqual(self.generators[202].generate.await_count, 2)
        self.assertFalse(await self.a.store.is_seen("second"))
        self.assertTrue(await self.b.store.is_seen("second"))

    async def test_removing_during_generation_stops_batch_without_restoring_watermark(self):
        await self.seed()
        self.source.fetch_projects.return_value = [project("first", ts=20), project("second", ts=21)]

        async def generate(_project):
            await self.a.store.remove_skill_id(180, [180])
            return "Already generated bid"

        self.generators[101].generate.side_effect = generate
        await self.notifier._tick()
        a_calls = [call.kwargs for call in self.bot.send_message.await_args_list if call.kwargs["chat_id"] == "101"]
        self.assertEqual(len(a_calls), 1)
        self.generators[101].generate.assert_awaited_once()
        self.assertEqual(self.generators[202].generate.await_count, 2)
        self.assertFalse(self.a.store.has_watermark(180))
        self.assertEqual(await self.a.store.recent_projects(), [])

    async def test_stop_during_generation_does_not_send_pending_bid(self):
        await self.seed()

        async def generate(_project):
            await self.a.store.set_active(False)
            return "Already generated bid"

        self.generators[101].generate.side_effect = generate
        await self.notifier._tick()
        a_calls = [call.kwargs for call in self.bot.send_message.await_args_list if call.kwargs["chat_id"] == "101"]
        self.assertEqual(len(a_calls), 1)
        self.assertNotIn("reply_to_message_id", a_calls[0])
        self.assertFalse(await self.a.store.is_active())
        self.assertTrue(await self.b.store.is_seen("42"))

    async def test_category_rename_during_batch_applies_to_next_message(self):
        await self.seed()
        self.source.fetch_projects.return_value = [project("first", ts=20), project("second", ts=21)]

        async def generate(_project):
            await self.a.store.set_category_name(180, "Updated category")
            return "Bid"

        self.generators[101].generate.side_effect = generate
        await self.notifier._tick()
        a_projects = [call.kwargs for call in self.bot.send_message.await_args_list
                      if call.kwargs["chat_id"] == "101" and "reply_to_message_id" not in call.kwargs]
        self.assertEqual(len(a_projects), 2)
        self.assertIn("Updated category", a_projects[1]["text"])
        self.assertEqual(self.generators[101].generate.await_args_list[1].args[0].category_name, "Updated category")

    async def test_mute_during_primary_screen_prevents_delivery(self):
        await self.seed()

        async def screen(_project):
            await self.a.store.toggle_muted_skill_id(180)
            return SimpleNamespace(allowed=True)

        notifier = NotifierLoop(
            self.bot, self.a.store, self.source, await self.a.settings(),
            screener=Mock(screen=AsyncMock(side_effect=screen)),
        )
        await notifier._tick([project()], {180})
        self.bot.send_message.assert_not_awaited()

    async def test_failed_project_with_equal_timestamp_is_retried(self):
        await self.seed()
        notifier = NotifierLoop(self.bot, self.a.store, self.source, await self.a.settings())
        notifier._send_project = AsyncMock(side_effect=[True, False, True])
        projects = [project("first", ts=20), project("second", ts=20)]
        await notifier._tick(projects, {180})
        self.assertFalse(await self.a.store.is_seen("second"))
        await notifier._tick(projects, {180})
        self.assertEqual([call.args[0].id for call in notifier._send_project.await_args_list], ["first", "second", "second"])
        self.assertTrue(await self.a.store.is_seen("second"))

    async def test_initial_boundary_is_not_sent_on_next_tick(self):
        await self.notifier._tick()
        await self.notifier._tick()
        self.bot.send_message.assert_not_awaited()
        self.assertTrue(await self.a.store.is_seen("42"))

    async def test_unmuting_does_not_deliver_boundary_backlog(self):
        await self.seed()
        await self.a.store.toggle_muted_skill_id(180)
        await self.notifier._tick()
        await self.a.store.toggle_muted_skill_id(180)
        await self.notifier._tick()
        self.assertEqual({call.kwargs["chat_id"] for call in self.bot.send_message.await_args_list}, {"202"})

    async def test_new_category_baseline_does_not_suppress_existing_category_order(self):
        await self.seed()
        await self.a.store.add_skill_id(99, [180])
        self.source.fetch_projects.return_value = [project(skill=99), project(skill=180)]
        self.source.successful_skill_ids = {99, 180}
        await self.notifier._tick()
        self.generators[101].generate.assert_awaited_once()
        self.assertEqual(self.generators[101].generate.await_args.args[0].skill_id, 180)
        await self.notifier._tick()
        self.generators[101].generate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
