import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from app.config import Settings
from app.storage.users import UserRegistry
from app.telegram.bot import build_dispatcher
from app.telegram import keyboards


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def stream_content(self, url, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if method.__api_method__ in {"sendMessage", "editMessageText"}:
            return Message(
                message_id=len(self.requests), date=datetime.now(timezone.utc),
                chat=Chat(id=int(method.chat_id), type="private"), text=method.text,
                from_user=User(id=123456, is_bot=True, first_name="Bot"),
            )
        return True


class DispatcherFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_user_registration_profile_prompt_and_stop_through_real_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                _env_file=None, telegram_bot_token="123456:ABC", freelancehunt_token="test",
                state_file=Path(tmp) / "state.json", ai_enabled=False,
            )
            registry = UserRegistry(settings)
            session = RecordingSession()
            bot = Bot("123456:ABC", session=session)
            dp = build_dispatcher(settings, registry, lambda user: None, Mock())
            sequence = 0

            async def feed(uid, text=None, callback=None, group=False):
                nonlocal sequence
                sequence += 1
                user = User(id=uid, first_name=f"User {uid}", is_bot=False)
                chat = Chat(id=-100 if group else uid, type="group" if group else "private")
                message = Message(
                    message_id=sequence, date=datetime.now(timezone.utc), chat=chat,
                    from_user=user if callback is None else User(id=123456, is_bot=True, first_name="Bot"),
                    text=text or "Menu",
                    entities=[MessageEntity(type="bot_command", offset=0, length=len(text))]
                    if text and text.startswith("/") else None,
                )
                update = Update(
                    update_id=sequence,
                    message=message if callback is None else None,
                    callback_query=CallbackQuery(
                        id=str(sequence), from_user=user, chat_instance="test",
                        data=callback, message=message,
                    ) if callback is not None else None,
                )
                await dp.feed_update(bot, update)

            try:
                await feed(101, "/start")
                await feed(202, "/start")
                await feed(101, callback=keyboards.CALLBACK_PROFILE_NAME)
                await feed(202, callback=keyboards.CALLBACK_PROFILE_NAME)
                await feed(101, "Алиса")
                await feed(202, "Борис")
                await feed(101, callback=keyboards.CALLBACK_PROFILE_PORTFOLIO)
                await feed(101, "https://alice.example/portfolio")
                await feed(101, callback=keyboards.CALLBACK_SYSTEM_PROMPT_EDIT)
                await feed(101, "Пиши кратко. Я дизайнер интерфейсов.")
                a, b = await registry.get(101), await registry.get(202)
                self.assertEqual(await a.store.profile(), {"name": "Алиса", "portfolio_url": "https://alice.example/portfolio"})
                self.assertEqual(await b.store.profile(), {"name": "Борис", "portfolio_url": ""})
                self.assertNotIn("Я дизайнер", a.system_prompt_path.read_text(encoding="utf-8"))
                self.assertNotIn("Я дизайнер", b.system_prompt_path.read_text(encoding="utf-8"))
                await feed(101, "/stop")
                await feed(101, "/settings")
                self.assertFalse(await a.store.is_active())
                self.assertTrue(await b.store.is_active())
                await feed(101, "/start")
                self.assertTrue(await a.store.is_active())
                await feed(303, "/start", group=True)
                self.assertEqual([u.user_id for u in await registry.users()], [101, 202])
            finally:
                await dp.storage.close()
                await dp.fsm.events_isolation.close()
                await bot.session.close()


if __name__ == "__main__":
    unittest.main()
