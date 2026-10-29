"""Safety and regression checks for the Midi low-RAM runtime."""

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import subprocess
import shutil
import unittest
from midivoice.browser_launch import low_resource_chromium_flags, voice_window_size

ROOT = Path(__file__).resolve().parents[1]


class OptimizationTests(TestCase):
    def test_voice_window_size_validated(self):
        with patch.dict("os.environ", {"MIDI_VOICE_WINDOW": "640,480"}):
            self.assertEqual(voice_window_size(), "640,480")
        for bad in ("5000,400", "bad", "640;rm -rf /", "0,0", "640,"):
            with (
                self.subTest(bad=bad),
                patch.dict("os.environ", {"MIDI_VOICE_WINDOW": bad}),
            ):
                with self.assertRaises(ValueError):
                    voice_window_size()

    def test_low_ram_flags_do_not_break_web_rtc_or_security(self):
        with patch.dict(
            "os.environ",
            {"MIDI_BROWSER_LOW_MEMORY": "1", "MIDI_HEADLESS_EXTENSIONS": "0"},
        ):
            flags = low_resource_chromium_flags()
        self.assertIn("--renderer-process-limit=1", flags)
        self.assertIn("--disable-extensions", flags)
        self.assertTrue(all(not f.startswith("--no-sandbox") for f in flags))
        self.assertTrue(all("webrtc" not in f.lower() for f in flags))
        self.assertTrue(all("site-isolation" not in f for f in flags))
        self.assertNotIn("--single-process", flags)

    def test_old_behavior_is_available_for_ab_testing(self):
        with patch.dict("os.environ", {"MIDI_BROWSER_LOW_MEMORY": "0"}):
            self.assertEqual(low_resource_chromium_flags(), [])

    def test_lightweight_monitor_does_not_invoke_python(self):
        text = (ROOT / "scripts/browser-performance-check.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("python3", text)
        self.assertIn("/proc/meminfo", text)
        self.assertIn("/proc/swaps", text)

    def test_zram_opt_in_only(self):
        runtime = (ROOT / "scripts/run-headless-voice.sh").read_text(encoding="utf-8")
        self.assertNotIn("midi-zram.sh enable", runtime)
        zram = (ROOT / "scripts/midi-zram.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$ACTION" = probe ]', zram)
        self.assertIn("Refusing to overwrite initialized zram0", zram)
        self.assertNotIn("swapoff", zram)

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell required")
    def test_shell_syntax(self):
        for f in (
            "scripts/browser-performance-check.sh",
            "scripts/midi-zram.sh",
            "scripts/run-headless-voice.sh",
        ):
            with self.subTest(file=f):
                result = subprocess.run(
                    ["sh", "-n", (ROOT / f).as_posix()], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
