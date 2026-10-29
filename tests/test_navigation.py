import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock

from midivoice.commands import parse_voice_command, VoiceCommand, validate_argument
from midivoice.navigation import BrowserNavigator, safe_project_url
from midivoice.state import StateStore
from midivoice.errors import BrowserError

PROJECT_ID = "g-p-1234567890abcdef1234567890abcdef"
PROJECT_URL = "https://chatgpt.com/g/" + PROJECT_ID + "/project"


class CommandTests(unittest.TestCase):
    def test_navigation_phrases(self):
        cases = {
            "Алиса, ChatGPT, новый чат": VoiceCommand("new"),
            "открой новую вкладку в чат джипити": VoiceCommand("new"),
            "Алиса, переключи ChatGPT в режим работы": VoiceCommand("mode-work"),
            "чат гпт режим чат": VoiceCommand("mode-chat"),
            "Алиса, открой проект Мой дом в ChatGPT": VoiceCommand(
                "project", "мой дом"
            ),
            "ChatGPT выйди из проекта": VoiceCommand("home"),
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(parse_voice_command(phrase, True), expected)
                self.assertIsNone(parse_voice_command(phrase, False))

    def test_unrelated_and_combined_commands_do_not_match(self):
        for phrase in (
            "открой проект Дом",
            "включи режим работы",
            "ChatGPT работа и музыка",
            "открой проект Дом и включи музыку в ChatGPT",
        ):
            self.assertIsNone(parse_voice_command(phrase, True))

    def test_argument_validation(self):
        for value in (None, "", " " * 10, "x" * 129, "bad\nname", [], 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_argument("project", value)
        with self.assertRaises(ValueError):
            validate_argument("mode-work", "argument")

    def test_only_project_page_links_are_saved(self):
        self.assertEqual(
            safe_project_url(PROJECT_URL + "?foo=bar#fragment"), PROJECT_URL
        )
        for url in (
            "https://evil.example" + PROJECT_URL[19:],
            "file:///project",
            "https://chatgpt.com/api/auth/session",
            PROJECT_URL + "/delete",
        ):
            self.assertIsNone(safe_project_url(url))

    def test_project_conversation_is_saved_without_query_parameters(self):
        from midivoice.state import safe_chat_url

        url = (
            PROJECT_URL.removesuffix("/project")
            + "/c/123e4567-e89b-12d3-a456-426614174000"
        )
        self.assertEqual(safe_chat_url(url + "?secret=value"), url)
        self.assertIsNone(safe_chat_url(url.replace("chatgpt.com", "evil.example")))


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def browser(self, directory):
        b = SimpleNamespace(
            state=StateStore(Path(directory)),
            settings=SimpleNamespace(voice_wait=60),
            inspect=AsyncMock(return_value={}),
            click=AsyncMock(),
            current_url=AsyncMock(return_value="https://chatgpt.com/"),
            send=AsyncMock(),
            click_point=AsyncMock(),
            evaluate=AsyncMock(),
        )
        return b

    async def test_named_project_is_confirmed_by_actual_page_url(self):
        with tempfile.TemporaryDirectory() as directory:
            b = self.browser(directory)
            b.evaluate.return_value = {
                "project_matches": 1,
                "target_project_id": PROJECT_ID,
                "point": {"x": 10, "y": 20},
            }
            b.current_url.side_effect = ["https://chatgpt.com/", PROJECT_URL]
            nav = BrowserNavigator(b)
            await nav.select_project("My home")
            self.assertEqual(nav.project_url, PROJECT_URL)
            b.click_point.assert_awaited_once()
            self.assertEqual(BrowserNavigator(b).project_name, "My home")

    async def test_ambiguous_project_does_not_click(self):
        with tempfile.TemporaryDirectory() as directory:
            b = self.browser(directory)
            b.evaluate.return_value = {"project_matches": 2}
            with self.assertRaisesRegex(BrowserError, "ambiguous"):
                await BrowserNavigator(b).select_project("Duplicate name")
            b.click_point.assert_not_awaited()

    async def test_new_conversation_keeps_project_and_finishes_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            b = self.browser(directory)
            b.inspect.side_effect = [{"end_button": True}, {"end_button": False}]
            nav = BrowserNavigator(b)
            nav.project_url = PROJECT_URL
            await nav.new_conversation()
            b.click.assert_awaited_once_with(ending=True)
            b.send.assert_awaited_once_with(
                "Page.navigate", {"url": PROJECT_URL}, page=True
            )

    async def test_untrusted_saved_navigation_cannot_set_a_target(self):
        with tempfile.TemporaryDirectory() as directory:
            b = self.browser(directory)
            (Path(directory) / "navigation.json").write_text(
                json.dumps(
                    {
                        "project_name": "Private",
                        "project_url": "https://evil.example/project",
                        "workspace_mode": "other",
                    }
                )
            )
            nav = BrowserNavigator(b)
            self.assertIsNone(nav.project_url)
            self.assertEqual(nav.workspace_mode, "chat")
