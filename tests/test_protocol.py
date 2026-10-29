import json
from pathlib import Path
import tempfile
import unittest

from midivoice.state import safe_chat_url, StateStore
from midivoice.controller import Controller


class UrlTests(unittest.TestCase):
    def test_safe(self):
        value = (
            "https://chatgpt.com/c/123e4567-e89b-12d3-a456-426614174000?share=secrets"
        )
        self.assertEqual(safe_chat_url(value), value.split("?")[0])

    def test_reject(self):
        for url in [
            "https://evil.com/c/123e4567-e89b-12d3-a456-426614174000",
            "https://chatgpt.com.evil.com/c/123e4567-e89b-12d3-a456-426614174000",
            "https://chatgpt.com/api/auth/session",
            "http://chatgpt.com/c/123e4567-e89b-12d3-a456-426614174000",
        ]:
            self.assertIsNone(safe_chat_url(url))

    def test_atomic_save(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            self.assertFalse(store.save_chat_url("https://invalid.com/"))
            url = "https://chatgpt.com/c/123e4567-e89b-12d3-a456-426614174000"
            self.assertTrue(store.save_chat_url(url))
            self.assertEqual(store.last_chat_url(), url)
            self.assertEqual(
                json.loads(store.conversation.read_text(encoding="utf-8"))["url"], url
            )


class FakeBrowser:
    def __init__(self):
        self.proc = None
        self.visits = []
        self.running = False
        self.is_voice = False
        self.memory_kb = None

    async def start(self, url=None):
        self.running = True
        self.proc = self
        self.visits.append(url)

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, value):
        self._running = value

    def poll(self):
        return None if self.running else 0

    async def navigate_home(self):
        self.visits.append("https://chatgpt.com/")

    async def inspect(self):
        return {
            "voice_button": not self.is_voice,
            "end_button": self.is_voice,
            "url": "https://chatgpt.com/",
            "ready": "complete",
        }

    async def click(self, ending=False):
        self.is_voice = not ending
        return True

    async def current_url(self):
        return "https://chatgpt.com/"

    async def stop(self, force=False):
        self.running = False
        self.proc = None


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_close_new(self):
        b = FakeBrowser()
        c = Controller(b)
        self.assertTrue((await c.handle("open"))["ok"])
        self.assertTrue((await c.handle("open"))["already_active"])
        self.assertEqual((await c.handle("status"))["mode"], "voice")
        self.assertEqual((await c.handle("close"))["mode"], "idle")
        self.assertTrue((await c.handle("new"))["ok"])
        self.assertEqual(b.visits[-1], "https://chatgpt.com/")
        await c.handle("close")

    async def test_unknown(self):
        with self.assertRaises(ValueError):
            await Controller(FakeBrowser()).handle("invalid")


if __name__ == "__main__":
    unittest.main()


class SpeechTests(unittest.TestCase):
    def test_explicit_wake(self):
        from midivoice.commands import parse_final_speech

        self.assertIsNone(parse_final_speech("Алиса, открой чат", False))
        for verb in ("запусти", "открой"):
            self.assertEqual(parse_final_speech(f"Алиса, {verb} ChatGPT", True), "open")
        for verb in ("закрой", "отключи"):
            self.assertEqual(
                parse_final_speech(f"Алиса, {verb} ChatGPT", True), "close"
            )
        self.assertEqual(parse_final_speech("открой чат джипити", True), "open")
        self.assertEqual(parse_final_speech("отключи чат гпт", True), "close")

    def test_no_accidental_trigger(self):
        from midivoice.commands import parse_final_speech

        for phrase in [
            "открой чат",
            "запусти чат",
            "создай новый чат",
            "верни алису",
            "закрой чат",
            "открой чат и запусти музыку",
            "что такое чат",
            "закройчат",
            "",
            "включи ChatGPT",
            "открой ChatGPT и музыку",
            "открой ChatGPT пожалуйста",
        ]:
            self.assertIsNone(parse_final_speech(phrase, True))
