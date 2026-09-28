import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.ai.policy import AI_OWNER_ID
from app.notifier.loop import NotifierLoop
from app.projects import Project


def project():
    return Project(id="42", url="https://example.test/42", title="Bot", budget="1000 UAH",
                   description="Build a bot", relative_time="", absolute_time="", published_ts=1, skill_id=180)


class ManualOnlyBidTest(unittest.IsolatedAsyncioTestCase):
    async def test_notification_never_generates_bid_and_button_only_for_owner(self):
        for uid in [None, 101, AI_OWNER_ID]:
            with self.subTest(uid=uid):
                generator = Mock(generate=AsyncMock())
                bot = AsyncMock()
                loop = NotifierLoop(bot, Mock(), Mock(), SimpleNamespace(telegram_chat_id=str(uid)),
                                    bid_generator=generator, user_id=uid)
                self.assertTrue(await loop._send_project(project()))
                generator.generate.assert_not_awaited()
                bot.send_message.assert_awaited_once()
                buttons = [b.callback_data for row in bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
                self.assertEqual("gen:42" in buttons, uid == AI_OWNER_ID)

    async def test_screener_needs_explicit_owner_context(self):
        for uid in [None, 101, AI_OWNER_ID]:
            screen = Mock(screen=AsyncMock(return_value=SimpleNamespace(allowed=False, stack="PHP")))
            loop = NotifierLoop(AsyncMock(), Mock(), Mock(), SimpleNamespace(telegram_chat_id=str(AI_OWNER_ID)),
                                screener=screen, user_id=uid)
            self.assertEqual(await loop._passes_primary_check(project()), uid != AI_OWNER_ID)
            self.assertEqual(screen.screen.await_count, int(uid == AI_OWNER_ID))
