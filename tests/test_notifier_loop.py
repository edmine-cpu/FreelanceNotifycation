import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

from app.ai import BidGenerationError
from app.llm import QuotaExceededError
from app.notifier.loop import NotifierLoop
from app.projects import Project
from app.telegram import formatting, keyboards


def _project() -> Project:
    return Project(
        id="project-42",
        url="https://example.test/project-42",
        title="Telegram bot",
        budget="1000 UAH",
        description="Build a bot",
        relative_time="now",
        absolute_time="2026-07-18 12:00",
        published_ts=1_752_840_000,
        skill_id=180,
        category_name="Bots",
        category_url="https://example.test/bots",
    )


def _notifier(*, bid_generator: Mock) -> tuple[NotifierLoop, AsyncMock]:
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=777)
    notifier = NotifierLoop(
        bot,
        Mock(),
        Mock(),
        SimpleNamespace(telegram_chat_id="123456"),
        bid_generator=bid_generator,
    )
    return notifier, bot


class AutomaticBidTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_sends_generated_bid_as_reply(self) -> None:
        project = _project()
        generator = Mock()
        generator.generate = AsyncMock(return_value="Generated bid")
        notifier, bot = _notifier(bid_generator=generator)

        delivered = await notifier._send_project(project)

        self.assertTrue(delivered)
        generator.generate.assert_awaited_once_with(project)
        self.assertEqual(
            bot.send_message.await_args_list,
            [
                call(
                    chat_id="123456",
                    text=formatting.format_project_notification(project),
                    reply_markup=keyboards.notification_keyboard(project.id),
                ),
                call(
                    chat_id="123456",
                    text="Generated bid",
                    reply_to_message_id=777,
                    reply_markup=keyboards.regen_bid_keyboard(project.id),
                    parse_mode=None,
                ),
            ],
        )

    async def test_generation_failure_does_not_fail_notification(self) -> None:
        project = _project()
        generator = Mock()
        generator.generate = AsyncMock(
            side_effect=BidGenerationError("provider failed")
        )
        notifier, bot = _notifier(bid_generator=generator)

        with self.assertLogs("app.notifier.loop", level="ERROR") as logs:
            delivered = await notifier._send_project(project)

        self.assertTrue(delivered)
        generator.generate.assert_awaited_once_with(project)
        bot.send_message.assert_awaited_once()
        self.assertIn("automatic bid generation failed", logs.output[0])

    async def test_quota_failure_does_not_fail_notification(self) -> None:
        project = _project()
        generator = Mock()
        generator.generate = AsyncMock(
            side_effect=QuotaExceededError("quota exhausted")
        )
        notifier, bot = _notifier(bid_generator=generator)

        with self.assertLogs("app.notifier.loop", level="WARNING") as logs:
            delivered = await notifier._send_project(project)

        self.assertTrue(delivered)
        generator.generate.assert_awaited_once_with(project)
        bot.send_message.assert_awaited_once()
        self.assertIn("ai quota exhausted", logs.output[0])


if __name__ == "__main__":
    unittest.main()
