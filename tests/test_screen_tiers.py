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


if __name__ == "__main__":
    unittest.main()
