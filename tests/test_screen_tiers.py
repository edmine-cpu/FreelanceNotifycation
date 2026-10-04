import unittest

from app.ai.screener import ScreenResult, _parse_verdict
from app.projects import Project
from app.telegram import formatting


def project():
    return Project("1", "https://freelancehunt.com/project/x/1.html", "CRM", "", "", "", "", 0)


class ScreenTierTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(_parse_verdict('{"decision":"core","stack":"FastAPI","reason":"CRM"}'),
                         ScreenResult(True, "FastAPI", "core", "CRM"))
        self.assertEqual(_parse_verdict('{"decision":"maybe","stack":"","reason":"стек не указан"}'),
                         ScreenResult(True, "", "maybe", "стек не указан"))
        self.assertFalse(_parse_verdict('{"decision":"skip","stack":"PHP","reason":"плагин WP"}').allowed)

    def test_old_and_invalid_formats_rejected(self):
        for raw in ['{"decision":"allow","stack":""}', '{"decision":"core","stack":""}',
                    '{"decision":"core","stack":"","reason":"' + "x" * 201 + '"}']:
            with self.assertRaises(ValueError):
                _parse_verdict(raw)

    def test_maybe_note_in_notification(self):
        self.assertNotIn("🤔", formatting.format_project_notification(project()))
        text = formatting.format_project_notification(project(), maybe_note="<смешанный стек>")
        self.assertIn("🤔 <i>Под вопросом: &lt;смешанный стек&gt;</i>", text)
        self.assertIn("🤔 <i>Под вопросом</i>", formatting.format_project_notification(project(), maybe_note=""))


class StartupRebuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_stored_projects_rescreened_on_start(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from app.ai.policy import AI_OWNER_ID
        from app.notifier.public import PublicNotifier

        def item(pid):
            return Project(pid, "", pid, "", "", "", "", 0, skill_id=180)

        stored = [item("old-core"), item("old-skip"), item("open")]
        verdict = {"old-core": True, "old-skip": False, "open": True}
        screener = Mock(screen=AsyncMock(side_effect=lambda p: SimpleNamespace(allowed=verdict[p.id])))

        def user(uid):
            store = Mock(add_projects=AsyncMock(), set_passed=AsyncMock(),
                         recent_projects=AsyncMock(return_value=stored))
            return SimpleNamespace(user_id=uid, store=store,
                                   settings=AsyncMock(return_value=SimpleNamespace(categories=[])))

        owner, other = user(AI_OWNER_ID), user(101)
        registry = Mock(users=AsyncMock(return_value=[other, owner]))
        source = Mock(fetch_projects=AsyncMock(return_value=[item("open")]))
        notifier = PublicNotifier(AsyncMock(), registry, source, SimpleNamespace(poll_interval=60),
                                  Mock(), screener)
        await notifier._rebuild_owner_filter()
        owner.store.set_passed.assert_awaited_once_with(["old-core", "open"])
        other.store.set_passed.assert_not_awaited()
        self.assertEqual(screener.screen.await_count, 3)


class AutoBidTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_orders_get_a_ready_bid_reply(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from app.ai.policy import AI_OWNER_ID
        from app.notifier import loop as loop_module
        from app.notifier.loop import NotifierLoop

        for uid, tier, expected in [(AI_OWNER_ID, "core", True), (AI_OWNER_ID, "maybe", False), (101, "core", False)]:
            with self.subTest(uid=uid, tier=tier):
                bot = AsyncMock()
                bot.send_message.return_value = SimpleNamespace(message_id=555)
                generator = Mock(generate_bid=AsyncMock(return_value={"rendered": "Здравствуйте.", "version": "v1"}))
                screener = Mock(screen=AsyncMock(return_value=SimpleNamespace(allowed=True, tier=tier, reason="")))
                notifier = NotifierLoop(bot, Mock(), Mock(), SimpleNamespace(telegram_chat_id="1", ai_auto_bid=True),
                                        screener=screener, bid_generator=generator, user_id=uid)
                await notifier._passes_primary_check(project())
                self.assertTrue(await notifier._send_project(project()))
                await asyncio.gather(*list(loop_module._BACKGROUND))
                self.assertEqual(generator.generate_bid.await_count, int(expected))
                self.assertEqual(bot.send_message.await_count, 1 + int(expected))
                if expected:
                    reply = bot.send_message.await_args_list[1].kwargs
                    self.assertEqual((reply["text"], reply["reply_to_message_id"]), ("Здравствуйте.", 555))
                    buttons = [b.callback_data for row in reply["reply_markup"].inline_keyboard for b in row]
                    self.assertIn("sent:1", buttons)


if __name__ == "__main__":
    unittest.main()
