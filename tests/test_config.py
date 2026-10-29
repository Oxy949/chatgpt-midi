import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from midivoice.config import Settings, alpine_root
from midivoice.browser_launch import voice_launch_flags
from midivoice.errors import BrowserError
from midivoice.state import StateStore


class SettingsTests(unittest.TestCase):
    def test_environment_read_at_call_time(self):
        with patch.dict(
            os.environ, {"MIDI_STATE_DIR": "/first", "MIDI_ALPINE_ROOT": "/alpine1"}
        ):
            first = Settings.from_env()
            self.assertEqual(alpine_root(), Path("/alpine1"))
        with patch.dict(
            os.environ, {"MIDI_STATE_DIR": "/second", "MIDI_ALPINE_ROOT": "/alpine2"}
        ):
            second = Settings.from_env()
            self.assertEqual(alpine_root(), Path("/alpine2"))
        self.assertEqual(first.socket_path, Path("/first/control.sock"))
        self.assertEqual(second.profile_dir, Path("/second/browser-profile"))

    def test_quoted_extra_flags(self):
        settings = Settings.from_env(
            {"MIDI_CHROMIUM_EXTRA_FLAGS": '--lang=ru "--host-resolver-rules=MAP a b"'}
        )
        self.assertEqual(
            settings.extra_flags, ("--lang=ru", "--host-resolver-rules=MAP a b")
        )

    def test_invalid_configuration(self):
        for env in (
            {"MIDI_VOICE_WAIT_SECONDS": "0"},
            {"MIDI_BROWSER_MODE": "wrong"},
            {"MIDI_VOICE_WINDOW": "9999,3"},
            {"MIDI_CHROMIUM_EXTRA_FLAGS": '"unfinished'},
        ):
            with self.subTest(env=env), self.assertRaises(ValueError):
                Settings.from_env(env)

    def test_launch_uses_explicit_settings(self):
        settings = Settings.from_env(
            {
                "MIDI_BROWSER_MODE": "visible",
                "DISPLAY": ":99",
                "MIDI_BROWSER_PROFILE_DIR": "/profile",
            }
        )
        with patch.dict(os.environ, {"MIDI_BROWSER_MODE": "headless"}, clear=True):
            flags = voice_launch_flags("https://chatgpt.com/", settings)
        self.assertIn(f"--user-data-dir={settings.profile_dir}", flags)
        self.assertNotIn("--headless=new", flags)
        self.assertNotIn("--disable-extensions", flags)
        self.assertFalse(any(flag.startswith("--app=") for flag in flags))

    def test_headless_preserves_real_audio_and_sandbox(self):
        flags = voice_launch_flags("https://chatgpt.com/", Settings.from_env({}))
        self.assertIn("--headless=new", flags)
        self.assertIn("--remote-debugging-pipe", flags)
        self.assertIn("--force-prefers-reduced-motion", flags)
        self.assertIn("--disable-background-timer-throttling", flags)
        self.assertIn("--disable-renderer-backgrounding", flags)
        for forbidden in (
            "--no-sandbox",
            "--use-fake-device-for-media-stream",
            "--remote-debugging-port",
        ):
            self.assertFalse(any(flag.startswith(forbidden) for flag in flags))

    def test_visible_requires_display(self):
        with self.assertRaises(BrowserError):
            voice_launch_flags(
                "https://chatgpt.com/",
                Settings.from_env({"MIDI_BROWSER_MODE": "visible"}),
            )


class StateTests(unittest.TestCase):
    def test_corrupt_or_unexpected_state_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            for value in (
                "broken",
                "null",
                "[]",
                '{"url":42}',
                '{"url":"https://evil.com/"}',
            ):
                store.conversation.write_text(value, encoding="utf-8")
                self.assertEqual(store.last_chat_url(), "https://chatgpt.com/")
                store.diagnostic.write_text(value, encoding="utf-8")
                result = store.read_diagnostic()
                self.assertTrue(result is None or isinstance(result, dict))

    def test_failed_replace_preserves_previous_state_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            store.write_diagnostic({"issue": "old"})
            with patch("midivoice.state.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store.write_diagnostic({"issue": "new"})
            self.assertEqual(store.read_diagnostic(), {"issue": "old"})
            self.assertEqual(list(Path(directory).iterdir()), [store.diagnostic])

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_replacing_public_file_creates_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            store.diagnostic.touch(mode=0o644)
            store.write_diagnostic({"issue": "test"})
            self.assertEqual(store.diagnostic.stat().st_mode & 0o777, 0o600)
