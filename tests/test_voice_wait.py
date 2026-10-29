"""Wait for the real ChatGPT interface on slow 1GB ARM devices."""

import asyncio
import os
from unittest.mock import AsyncMock, patch
import unittest

from midivoice import browser, controller as cli
from midivoice.config import voice_wait_seconds


class FakeProc:
    def poll(self):
        return None


class FakeBrowser:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.proc = FakeProc()
        self.running = True
        self.memory_kb = None
        self.navigate_home = AsyncMock()
        self.clicked = False
        self.click = AsyncMock(side_effect=self._click)
        self.stop = AsyncMock()
        self.start = AsyncMock()

    async def inspect(self):
        if self.clicked:
            return {"end_button": True}
        try:
            return next(self.pages)
        except StopIteration:
            return {"url": "https://chatgpt.com/", "ready": "complete", "buttons": 0}

    def _click(self, ending=False):
        self.clicked = not ending
        return True


class BrowserVoiceWaitTests(unittest.TestCase):
    def test_voice_wait_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(voice_wait_seconds(), 360)
        for text in ("60", "900"):
            with patch.dict(os.environ, {"MIDI_VOICE_WAIT_SECONDS": text}):
                self.assertEqual(voice_wait_seconds(), int(text))
        for text in ("0", "nope", "901"):
            with patch.dict(os.environ, {"MIDI_VOICE_WAIT_SECONDS": text}):
                with self.assertRaises(ValueError):
                    voice_wait_seconds()

    def test_selector_matches_plain_voice_and_includes_button_roles(self):
        js = browser.voice_inspector_script()
        self.assertIn(r"/\bvoice\b", js)
        self.assertNotIn(r"/\\bvoice\\b", js)
        self.assertIn('button,[role="button"]', js)
        self.assertIn("dictationRx", js)
        self.assertNotIn("__CLICK__", js)

    def test_slow_chatgpt_does_not_reload_or_give_up_before_button(self):
        pages = [
            {"url": "https://chatgpt.com/", "ready": "loading", "buttons": 2}
            for _ in range(10)
        ]
        pages.append(
            {
                "url": "https://chatgpt.com/",
                "ready": "complete",
                "buttons": 26,
                "voice_button": True,
            }
        )
        b = FakeBrowser(pages)
        ctl = cli.Controller(b)
        events = []

        async def progress(evt):
            events.append(evt)

        async def run():
            with (
                patch.object(ctl, "sleep", new=AsyncMock()),
                patch.object(ctl, "clock", side_effect=[i * 5 for i in range(120)]),
            ):
                return await ctl._open(progress=progress)

        result = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertTrue(result["click_sent"])
        self.assertEqual(ctl.mode, "voice")
        b.navigate_home.assert_not_awaited()  # Reloading resets the costly page load!
        b.click.assert_awaited_once()
        self.assertTrue(any(evt.get("event") == "progress" for evt in events))

    def test_diagnostic_redacts_page_url_and_chat_text(self):
        info = cli.voice_diagnostic(
            {
                "url": "https://chatgpt.com/c/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "buttons": 3,
                "chat_text": "secret",
                "cookies": "do-not-leak",
            },
            "test",
        )
        self.assertEqual(info["site"], "chatgpt.com")
        self.assertNotIn("url", info)
        self.assertNotIn("chat_text", info)
        self.assertNotIn("cookies", info)


if __name__ == "__main__":
    unittest.main()
