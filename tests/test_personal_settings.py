import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import SecretStr

from app.config import Settings
from app.projects import Project
from app.storage import StateStore
from app.telegram import formatting, keyboards
from app.telegram.handlers import callbacks, commands, ai_actions
from app.ai.policy import AI_OWNER_ID
from app.ai.store import AIStore
from app.telegram.views import projects_page_view, start_view


def settings_for(user_id: int = 11, skill_ids: str = "180") -> Settings:
    return Settings(
        _env_file=None, telegram_bot_token="token", telegram_chat_id=str(user_id),
        freelancehunt_token="token", skill_ids=skill_ids, ai_enabled=False,
    )


def project_for(skill_id: int, project_id: str = "project-1") -> Project:
    return Project(
        id=project_id, url=f"https://example.com/{project_id}", title=project_id,
        budget="", description="Example", relative_time="", absolute_time="",
        published_ts=10, skill_id=skill_id, category_name="Shared source name",
    )


def message_for(text: str = "", user_id: int = 11):
    return SimpleNamespace(
        chat=SimpleNamespace(id=user_id), from_user=SimpleNamespace(id=user_id),
        text=text, message_id=3, answer=AsyncMock(), reply=AsyncMock(), delete=AsyncMock(),
        edit_text=AsyncMock(), bot=SimpleNamespace(edit_message_text=AsyncMock()),
    )


def callback_for(data: str, user_id: int = 11):
    return SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=user_id), message=message_for(user_id=user_id),
        answer=AsyncMock(),
    )


def state_for(state=None):
    return SimpleNamespace(
        get_data=AsyncMock(return_value={}), get_state=AsyncMock(return_value=state),
        set_state=AsyncMock(), update_data=AsyncMock(), clear=AsyncMock(),
    )


class PersonalValidationTest(unittest.TestCase):
    def test_portfolio_accepts_web_links_and_clear(self):
        for value in ["https://example.com/my-portfolio?a=1&b=2", "http://behance.net/name", "https://пример.укр/работы"]:
            self.assertEqual(callbacks._validate_portfolio_url(value), value)
        self.assertEqual(callbacks._validate_portfolio_url("-"), "")

    def test_portfolio_rejects_credentials_and_malformed_links(self):
        for value in [
            "", "example.com", "javascript:alert(1)", "https://", "https://user:pass@example.com",
            "https://user@example.com", "https://example.com:99999/", "https://example.com\n/",
            "https://example.com\\@evil.com", "https://bad_host.com/", "https://<bad>.com/",
            "https://example.com/" + "a" * 500,
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                callbacks._validate_portfolio_url(value)

    def test_name_validation_and_html_escaping(self):
        self.assertEqual(callbacks._validate_profile_name("  Анна   Иванова  "), "Анна Иванова")
        self.assertEqual(callbacks._validate_profile_name("-"), "")
        for value in ["", "x" * 81, "ab\x00cd"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                callbacks._validate_profile_name(value)
        rendered = formatting.format_profile({"name": "<b>Анна</b>", "portfolio_url": "https://example.com?a=1&b=2"})
        self.assertIn("&lt;b&gt;Анна&lt;/b&gt;", rendered)
        self.assertIn("a=1&amp;b=2", rendered)

    def test_new_user_menu_has_private_onboarding_and_separate_prompt_examples(self):
        settings = settings_for().model_copy(update={"skill_ids": ""})
        text, markup = start_view(settings)
        self.assertIn("пока не выбраны", text)
        self.assertIn("только для тебя", text)
        data = {button.callback_data for row in keyboards.settings_keyboard().inline_keyboard for button in row}
        self.assertIn(keyboards.CALLBACK_PROFILE, data)
        self.assertNotIn(keyboards.CALLBACK_SYSTEM_PROMPT, data)
        self.assertNotIn(keyboards.CALLBACK_PROMPT_JSON, data)

    def test_all_projects_view_hides_removed_categories(self):
        text, markup = projects_page_view([project_for(180, "own"), project_for(99, "removed")], 0, settings_for())
        data = {button.callback_data for row in markup.inline_keyboard for button in row}
        self.assertIn("show:own", data)
        self.assertNotIn("show:removed", data)
        self.assertIn("Всего: 1", text)

    def test_long_category_names_keep_every_menu_within_telegram_limit(self):
        label = ", ".join(["😀" * 80] * 30)
        profile = {"name": "😀" * 80, "portfolio_url": "https://example.com/" + "x" * 480}
        menus = [
            formatting.format_start_menu(label), formatting.format_settings_menu(label, 30, profile),
            formatting.format_category_names(label), formatting.format_category_notifications(label),
            formatting.format_remove_categories(label),
        ]
        for menu in menus:
            self.assertLessEqual(callbacks._telegram_length(menu), 4096)
            self.assertIn("…", menu)

    def test_failed_atomic_prompt_write_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt.md"
            path.write_text("original", encoding="utf-8")
            with patch.object(callbacks.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    callbacks._write_personal_prompt(path, "replacement")
            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(Path(directory).iterdir()), [path])


class PersonalHandlersTest(unittest.IsolatedAsyncioTestCase):
    async def test_profile_edit_only_changes_current_user(self):
        with tempfile.TemporaryDirectory() as directory:
            first = StateStore(Path(directory) / "first.json")
            second = StateStore(Path(directory) / "second.json")
            await second.set_profile(name="Другой пользователь", portfolio_url="https://other.example.com")
            message = message_for("Моё имя")
            state = state_for(callbacks.SettingsFlow.awaiting_profile_name.state)
            await callbacks.handle_profile_message(message, settings_for(), first, state)
            self.assertEqual((await first.profile())["name"], "Моё имя")
            self.assertEqual((await second.profile())["name"], "Другой пользователь")
            state.clear.assert_awaited_once()
            await callbacks.handle_profile_message(message_for("-"), settings_for(), first, state)
            self.assertEqual((await first.profile())["name"], "")

    async def test_invalid_portfolio_does_not_change_profile(self):
        store = SimpleNamespace(set_profile=AsyncMock())
        state = state_for(callbacks.SettingsFlow.awaiting_profile_portfolio.state)
        await callbacks.handle_profile_message(message_for("https://user:password@example.com"), settings_for(), store, state)
        store.set_profile.assert_not_awaited()
        state.clear.assert_not_awaited()

    async def test_prompt_edit_writes_only_injected_path_and_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mine.md"
            other = Path(directory) / "other.md"
            other.write_text("Other prompt", encoding="utf-8")
            generator = Mock()
            state = state_for()
            await callbacks.handle_system_prompt_message(
                message_for("Пиши кратко и по делу.", AI_OWNER_ID), settings_for(AI_OWNER_ID), generator, path, state,
            )
            self.assertEqual(path.read_text(encoding="utf-8").strip(), "Пиши кратко и по делу.")
            self.assertEqual(other.read_text(encoding="utf-8"), "Other prompt")
            generator.reload_prompt.assert_called_once_with()
            state.clear.assert_awaited_once()

    async def test_invalid_prompt_preserves_file_and_edit_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mine.md"
            path.write_text("Original", encoding="utf-8")
            for invalid in [" ", "x" * 3501, "😀" * 1751]:
                with self.subTest(length=len(invalid)):
                    generator = Mock()
                    state = state_for()
                    await callbacks.handle_system_prompt_message(message_for(invalid, AI_OWNER_ID), settings_for(AI_OWNER_ID), generator, path, state)
                    self.assertEqual(path.read_text(encoding="utf-8"), "Original")
                    generator.reload_prompt.assert_not_called()
                    state.clear.assert_not_awaited()

    async def test_prompt_view_sends_plain_text_in_telegram_sized_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mine.md"
            path.write_text("<my instructions>" + "😀" * 4000, encoding="utf-8")
            callback = callback_for(keyboards.CALLBACK_SYSTEM_PROMPT, AI_OWNER_ID)
            await callbacks.handle_system_prompt(callback, settings_for(AI_OWNER_ID), path, state_for())
            self.assertGreater(callback.message.answer.await_count, 1)
            for call in callback.message.answer.await_args_list:
                self.assertIsNone(call.kwargs["parse_mode"])
                self.assertLessEqual(callbacks._telegram_length(call.args[0]), 3500)

    async def test_large_legacy_examples_are_viewable_in_chunks(self):
        text = '{"examples": ["' + "😀" * 4000 + '"]}'
        callback = callback_for(keyboards.CALLBACK_PROMPT_JSON, AI_OWNER_ID)
        with patch.object(callbacks, "read_prompt_json", return_value=text):
            await callbacks.handle_prompt_json(callback, settings_for(AI_OWNER_ID), Path("unused"), state_for())
        sent = callback.message.answer.await_args_list
        self.assertEqual("".join(call.args[0] for call in sent), text)
        self.assertGreater(len(sent), 1)
        for call in sent:
            self.assertLessEqual(callbacks._telegram_length(call.args[0]), 3500)
            self.assertIsNone(call.kwargs["parse_mode"])
        self.assertIsNotNone(sent[-1].kwargs["reply_markup"])

    async def test_public_user_history_does_not_use_legacy_ai_filter(self):
        settings = settings_for()
        settings.ai_enabled = True
        settings = settings.model_copy(update={"anthropic_api_key": SecretStr("token"), "primary_filter_enabled": False})
        store = SimpleNamespace(recent_projects=AsyncMock(return_value=[project_for(180)]), passed_ids=AsyncMock())
        callback = callback_for("list:all:0")
        await callbacks.handle_list_page(callback, settings, store)
        store.passed_ids.assert_not_awaited()
        self.assertIn("Всего: 1", callback.message.edit_text.await_args.args[0])

    async def test_raw_callback_rejects_unsubscribed_category_before_fetch(self):
        for skill_id in [99, 0, -1, 2147483648]:
            with self.subTest(skill_id=skill_id):
                callback = callback_for(f"raw:{skill_id}:0")
                source = SimpleNamespace(fetch_category=AsyncMock())
                store = SimpleNamespace(add_projects=AsyncMock())
                await callbacks.handle_raw_page(callback, settings_for(), store, source)
                source.fetch_category.assert_not_awaited()
                store.add_projects.assert_not_awaited()
                self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_raw_fetch_preserves_private_category_name_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for()
            settings.category_names = {180: "Мои боты"}
            store = StateStore(Path(directory) / "mine.json")
            source = SimpleNamespace(fetch_category=AsyncMock(return_value=[project_for(180), project_for(99, "foreign")]))
            await callbacks.handle_raw_page(callback_for("raw:180:0"), settings, store, source)
            projects = await store.recent_projects()
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0].category_name, "Мои боты")
            self.assertEqual(projects[0].category_url, settings.categories[0].listing_url)

    async def test_removed_project_cannot_be_shown_generated_or_regenerated(self):
        store = SimpleNamespace(find_project=AsyncMock(return_value=project_for(99)))
        generator = SimpleNamespace(generate=AsyncMock())
        callback = callback_for("show:project-1")
        await callbacks.handle_show_project(callback, settings_for(), store)
        callback.message.answer.assert_not_awaited()

    async def test_tampered_mute_and_list_do_not_use_store(self):
        store = SimpleNamespace(toggle_muted_skill_id=AsyncMock(), recent_projects=AsyncMock())
        await callbacks.handle_toggle_category_notifications(callback_for("settings:toggle_mute:99"), settings_for(), store)
        await callbacks.handle_list_page(callback_for("list:99:0"), settings_for(), store)
        store.toggle_muted_skill_id.assert_not_awaited()
        store.recent_projects.assert_not_awaited()

    async def test_sender_cannot_use_another_chat_context(self):
        callback = callback_for("settings:toggle_mute:180")
        callback.from_user.id = 22
        store = SimpleNamespace(toggle_muted_skill_id=AsyncMock())
        await callbacks.handle_toggle_category_notifications(callback, settings_for(), store)
        store.toggle_muted_skill_id.assert_not_awaited()

    async def test_remove_final_category_does_not_restore_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "mine.json")
            settings = settings_for()
            await callbacks.handle_remove_category(callback_for("settings:remove_category:180"), settings, store, state_for())
            self.assertEqual(settings.categories, [])
            self.assertEqual(await store.skill_ids([180]), [])

    async def test_category_limit_and_invalid_id_do_not_persist(self):
        store = SimpleNamespace(add_skill_id=AsyncMock())
        cases = [(settings_for(skill_ids=",".join(str(i) for i in range(1, 31))), "31"), (settings_for(), "2147483648")]
        for settings, text in cases:
            await callbacks.handle_category_id_message(message_for(text), settings, store, state_for())
        store.add_skill_id.assert_not_awaited()

    async def test_stop_then_start_toggles_only_current_store(self):
        store = SimpleNamespace(set_active=AsyncMock())
        await commands.handle_stop(message_for(), store, state_for())
        await commands.handle_start(message_for(), settings_for(), store, state_for())
        self.assertEqual([call.args[0] for call in store.set_active.await_args_list], [False, True])


if __name__ == "__main__":
    unittest.main()
