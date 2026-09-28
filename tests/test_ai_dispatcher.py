import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from app.ai import BidGenerator
from app.ai.client import AnthropicClient
from app.ai.policy import AI_OWNER_ID
from app.ai.pricing import AtomicQuoteStore
from app.ai.store import AIStore
from app.config import Settings
from app.storage.users import UserRegistry
from app.telegram import keyboards
from app.telegram.bot import build_dispatcher
from test_anthropic_client import response
from test_dispatcher_flow import RecordingSession
from test_public_notifier import project


class FailingSession(RecordingSession):
    fail_edit = False
    fail_bid = False

    async def make_request(self, bot, method, timeout=None):
        if self.fail_edit and method.__api_method__ == "editMessageText":
            self.fail_edit = False
            raise TelegramBadRequest(method=method, message="temporary edit failure")
        if self.fail_bid and method.__api_method__ == "sendMessage" and "Ориентировочные цена" in method.text:
            self.fail_bid = False
            raise TelegramBadRequest(method=method, message="temporary send failure")
        return await super().make_request(bot, method, timeout)


class OwnerFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, telegram_bot_token="123456:ABC", freelancehunt_token="test",
                                 anthropic_api_key="test", state_file=root/"state.json")
        self.registry = UserRegistry(self.settings)
        self.owner = await self.registry.get(AI_OWNER_ID)
        self.other = await self.registry.get(111)
        for context in (self.owner, self.other):
            await context.store.add_projects([project(), project("43")])
        self.store = AIStore(root/"ai.sqlite3")
        self.addCleanup(self.store.close)
        self.calls = []
        self.bad_response = False
        async def transport(request):
            payload = json.loads(request.content)
            self.calls.append(payload)
            await asyncio.sleep(.001)
            if self.bad_response:
                self.bad_response = False
                return response("bad JSON")
            data = {"prose": f"Вариант {len(self.calls)}: подключу API календаря к боту"}
            if "scope_tier" in payload["output_config"]["format"]["schema"]["properties"]:
                data["scope_tier"] = "8"
            return response(json.dumps(data, ensure_ascii=False))
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.addAsyncCleanup(self.http.aclose)
        client = AnthropicClient("test", "claude-opus-5-5", http=self.http, store=self.store, effort="low")
        self.generator = BidGenerator(client, user_id=AI_OWNER_ID, ai_store=self.store,
            quote_store=AtomicQuoteStore(self.owner.quote_path), system_prompt_path=self.owner.system_prompt_path,
            examples_path=self.owner.prompt_examples_path, profile_provider=self.owner.store.profile)
        self.factory = Mock(return_value=self.generator)
        self.session = FailingSession()
        self.bot = Bot("123456:ABC", session=self.session)
        self.dp = build_dispatcher(self.settings, self.registry, self.factory, Mock(), ai_store=self.store)
        self.sequence = 0
        async def close_dispatcher():
            await self.dp.storage.close()
            await self.dp.fsm.events_isolation.close()
            await self.bot.session.close()
            for router in self.dp.sub_routers:
                router._parent_router = None
            self.dp.sub_routers.clear()
        self.addAsyncCleanup(close_dispatcher)

    async def feed(self, text=None, callback=None, uid=AI_OWNER_ID, event_id=None, message_id=300, group=False):
        self.sequence += 1
        event_id = event_id or self.sequence
        sender = User(id=uid, is_bot=False, first_name="User")
        message = Message(message_id=event_id if callback is None else message_id,
            date=datetime.now(timezone.utc), chat=Chat(id=-1 if group else uid, type="group" if group else "private"),
            from_user=sender if callback is None else User(id=123456, is_bot=True, first_name="Bot"), text=text,
            entities=[MessageEntity(type="bot_command", offset=0, length=len(text))] if text and text.startswith("/") else None)
        update = Update(update_id=event_id, message=message if callback is None else None,
            callback_query=CallbackQuery(id=str(event_id), from_user=sender, chat_instance="test", data=callback, message=message) if callback else None)
        await self.dp.feed_update(self.bot, update)
        return event_id

    def latest_regen(self):
        for req in reversed(self.session.requests):
            markup = getattr(req, "reply_markup", None)
            if markup:
                for row in markup.inline_keyboard:
                    for b in row:
                        if (b.callback_data or "").startswith("regen:"):
                            return b.callback_data
        raise AssertionError("no regeneration button")

    def latest_retry(self):
        for req in reversed(self.session.requests):
            markup = getattr(req, "reply_markup", None)
            if markup:
                for row in markup.inline_keyboard:
                    for b in row:
                        if (b.callback_data or "").startswith("ai_retry:"):
                            return b.callback_data
        raise AssertionError("no retry button")

    async def begin(self, pid="42"):
        await self.feed(callback="gen:"+pid)
        await self.feed(callback=self.latest_regen())
        return self.store.waiting_revision(AI_OWNER_ID)

    async def test_prompt_text_skip_and_previous_price_preserved(self):
        pending = await self.begin()
        self.assertEqual(len(self.calls), 1)
        prompt = self.session.requests[-1]
        self.assertEqual(prompt.text, "Напишите корректировки для AI")
        self.assertEqual([b.text for row in prompt.reply_markup.inline_keyboard for b in row], ["Пропустить", "Отмена"])
        before = pending["previous"]["quote"]
        await self.feed(text="Короче, добавь вопрос про API")
        self.assertEqual(len(self.calls), 2)
        target = json.loads(self.calls[-1]["messages"][-1]["content"])
        self.assertEqual(target["corrections"], "Короче, добавь вопрос про API")
        self.assertEqual(target["previous_bid"], pending["previous"]["rendered"])
        self.assertEqual(self.session.requests[-1].__api_method__, "editMessageText")
        self.assertEqual(self.session.requests[-1].message_id, 300)
        await self.feed(callback=self.latest_regen())
        next_pending = self.store.waiting_revision(AI_OWNER_ID)
        self.assertEqual(next_pending["previous"]["quote"], before)
        await self.feed(callback="ai_skip:"+next_pending["id"])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(json.loads(self.calls[-1]["messages"][-1]["content"])["corrections"], "")
        self.assertEqual(self.store.revision(AI_OWNER_ID, next_pending["id"])["status"], "success")

    async def test_invalid_input_preserves_waiting_no_ai_reprice_explains(self):
        pending = await self.begin()
        for invalid in [None, "", " ", "X"*2001, "Уменьши цену до 100 долларов", "Пересчитай часы", "Зміни термін", "make it cheaper"]:
            await self.feed(text=invalid)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.store.waiting_revision(AI_OWNER_ID)["id"], pending["id"])
        self.assertIn("меняет только текст", self.session.requests[-1].text)

    async def test_cancel_commands_menus_expiry_stale_buttons_and_project_switch(self):
        pending = await self.begin()
        await self.feed(callback="ai_cancel:"+pending["id"])
        await self.feed(callback="ai_skip:"+pending["id"])
        self.assertEqual(len(self.calls), 1)
        for command in ["/settings", "/cancel", "/start", "/stop", "/help", "/unknown", "/ai_usage"]:
            await self.feed(callback=self.latest_regen())
            old = self.store.waiting_revision(AI_OWNER_ID)
            await self.feed(text=command)
            self.assertIsNone(self.store.waiting_revision(AI_OWNER_ID))
            await self.feed(callback="ai_skip:"+old["id"])
            self.assertEqual(len(self.calls), 1)
        await self.feed(callback=self.latest_regen())
        await self.feed(callback="settings")
        self.assertIsNone(self.store.waiting_revision(AI_OWNER_ID))
        await self.feed(callback=self.latest_regen())
        old = self.store.waiting_revision(AI_OWNER_ID)
        with self.store.db:
            self.store.db.execute("UPDATE revisions SET expires=? WHERE id=?", (time.time()-1, old["id"]))
        await self.feed(text="Короче")
        self.assertIn("истекло", self.session.requests[-1].text)
        await self.feed(callback=self.latest_regen())
        old = self.store.waiting_revision(AI_OWNER_ID)
        new = await self.begin("43")
        await self.feed(callback="ai_skip:"+old["id"])
        self.assertEqual(len(self.calls), 2)
        await self.feed(text="Для второго проекта")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.store.revision(AI_OWNER_ID, new["id"])["status"], "success")
        self.assertEqual(self.store.revision(AI_OWNER_ID, old["id"])["status"], "cancelled")

    async def test_concurrent_text_skip_and_replayed_updates_no_extra_call(self):
        pending = await self.begin()
        text_id = self.sequence+1
        await asyncio.gather(self.feed(text="Короче", event_id=text_id),
                             self.feed(callback="ai_skip:"+pending["id"]))
        self.assertEqual(len(self.calls), 2)
        await self.feed(callback=self.latest_regen())
        newest = self.store.waiting_revision(AI_OWNER_ID)
        await self.feed(text="Короче", event_id=text_id)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.store.waiting_revision(AI_OWNER_ID)["id"], newest["id"])
        event = await self.feed(callback="ai_skip:"+newest["id"])
        await self.feed(callback="ai_skip:"+newest["id"], event_id=event)
        self.assertEqual(len(self.calls), 3)

    async def test_regen_replayed_callback_does_not_create_new_operation(self):
        await self.feed(callback="gen:42")
        event = await self.feed(callback=self.latest_regen())
        pending = self.store.waiting_revision(AI_OWNER_ID)
        await self.feed(callback=self.latest_regen(), event_id=event)
        self.assertEqual(self.store.waiting_revision(AI_OWNER_ID)["id"], pending["id"])
        self.assertEqual(len(self.calls), 1)

    async def test_ai_failure_retains_previous_and_explicit_retry_context(self):
        pending = await self.begin()
        self.bad_response = True
        await self.feed(text="Пиши короче")
        self.assertIsNone(self.store.waiting_revision(AI_OWNER_ID))
        self.assertEqual(self.store.revision(AI_OWNER_ID, pending["id"])["status"], "failed")
        await self.feed(text="Постороннее сообщение")
        self.assertEqual(len(self.calls), 2)
        retry = self.latest_retry()
        event = await self.feed(callback=retry)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(json.loads(self.calls[-1]["messages"][-1]["content"])["corrections"], "Пиши короче")
        await self.feed(callback=retry, event_id=event)
        self.assertEqual(len(self.calls), 3)
        self.assertIsNotNone(self.store.bid(AI_OWNER_ID, "42", pending["previous"]["version"]))

    async def test_telegram_failure_keeps_result_resend_has_zero_ai(self):
        pending = await self.begin()
        self.session.fail_edit = True
        await self.feed(text="Короче")
        self.assertEqual(len(self.calls), 2)
        failed = self.store.revision(AI_OWNER_ID, pending["id"])
        self.assertEqual(failed["status"], "delivery_failed")
        await self.feed(callback=self.latest_retry())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.store.revision(AI_OWNER_ID, pending["id"])["status"], "success")
        self.session.fail_bid = True
        await self.feed(callback="gen:43")
        self.assertEqual(len(self.calls), 3)
        await self.feed(callback="gen:43")
        self.assertEqual(len(self.calls), 3)

    async def test_other_users_old_callbacks_prompts_usage_zero_ai_and_private_history_works(self):
        pending = await self.begin()
        self.calls.clear()
        self.factory.reset_mock()
        original = self.other.system_prompt_path.read_bytes()
        for data in ["gen:42", self.latest_regen(), "ai_skip:"+pending["id"], "ai_retry:"+pending["id"],
                     "settings:system_prompt", "settings:system_prompt_edit", "settings:prompt_json", "settings:prompt_edit", "ai_usage"]:
            await self.feed(callback=data, uid=111)
            self.assertIn("только владельцу", self.session.requests[-1].text)
        for command in ["/start", "/settings", "/ai_usage"]:
            await self.feed(text=command, uid=111)
        await self.feed(callback="show:42", uid=111)
        request = self.session.requests[-2]  # send followed by callback answer
        self.assertEqual(request.__api_method__, "sendMessage")
        self.assertFalse(any((b.callback_data or "").startswith("gen:") for row in request.reply_markup.inline_keyboard for b in row))
        self.assertEqual(self.calls, [])
        self.factory.assert_not_called()
        self.assertEqual(self.other.system_prompt_path.read_bytes(), original)
        await self.feed(callback="gen:42", uid=AI_OWNER_ID, group=True)
        self.assertEqual(self.calls, [])

    async def test_removed_project_rejected_and_restart_restores_waiting(self):
        pending = await self.begin()
        # FSM state is irrelevant: the durable record owns the pending input.
        state = self.dp.fsm.get_context(bot=self.bot, chat_id=AI_OWNER_ID, user_id=AI_OWNER_ID)
        await state.clear()
        await self.feed(text="Новый текст")
        self.assertEqual(len(self.calls), 2)
        await self.owner.store.remove_skill_id(180, [180])
        await self.feed(callback="gen:42")
        await self.feed(callback=self.latest_regen())
        self.assertEqual(len(self.calls), 2)

    async def test_text_length_and_part_edits_are_not_mistaken_for_repricing(self):
        await self.begin()
        for text in ["Сделай 5 строк", "Сделай вводную часть короче", "Сделай короче, цену не меняй"]:
            before = len(self.calls)
            await self.feed(text=text)
            self.assertEqual(len(self.calls), before+1)
            self.assertEqual(json.loads(self.calls[-1]["messages"][-1]["content"])["corrections"], text)
            await self.feed(callback=self.latest_regen())
