import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from midivoice.indicators import COLORS, LOGO, SYNC, LogoIndicator


@unittest.skipUnless(os.name == "posix", "Linux sysfs LED names contain colons")
class IndicatorTests(unittest.TestCase):
    def setup_leds(self, directory):
        root = Path(directory) / "leds"
        for name in (*LOGO, SYNC):
            folder = root / name
            folder.mkdir(parents=True)
            trigger = "ledfb-logo-sync" if name == SYNC else "ledfb-logo"
            (folder / "trigger").write_text(f"none timer [{trigger}]")
            (folder / "brightness").write_text("5")
            (folder / "max_brightness").write_text("255")
        return root

    def assert_restored(self, leds):
        for name in (*LOGO, SYNC):
            trigger = "ledfb-logo-sync" if name == SYNC else "ledfb-logo"
            self.assertEqual((leds / name / "trigger").read_text().strip(), trigger)
            self.assertEqual((leds / name / "brightness").read_text().strip(), "5")

    def test_colors_and_restore_do_not_touch_ring_or_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            leds = self.setup_leds(directory)
            untouched = leds / "cucumber:red:ring0"
            untouched.mkdir()
            (untouched / "brightness").write_text("77")
            indicator = LogoIndicator(Path(directory) / "state", leds)
            for mode in ("starting", "voice", "verification"):
                indicator.show(mode)
                self.assertIsNone(indicator.error)
                for name, value in zip(LOGO, COLORS[mode] * 6):
                    self.assertEqual(
                        int((leds / name / "brightness").read_text()), value
                    )
                self.assertEqual((untouched / "brightness").read_text(), "77")
            indicator.show("idle")
            self.assert_restored(leds)
            self.assertFalse(indicator.journal.exists())

    def test_restart_recovers_original_firmware_triggers(self):
        with tempfile.TemporaryDirectory() as directory:
            leds = self.setup_leds(directory)
            state = Path(directory) / "state"
            first = LogoIndicator(state, leds)
            first.show("voice")
            replacement = LogoIndicator(state, leds)
            replacement.show("idle")
            self.assert_restored(leds)
            self.assertFalse(first.journal.exists())

    def test_partial_write_failure_restores_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            leds = self.setup_leds(directory)
            indicator = LogoIndicator(Path(directory) / "state", leds)
            write = indicator._write
            failed = False

            def fail_once(name, attribute, value):
                nonlocal failed
                if attribute == "brightness" and not failed:
                    failed = True
                    raise OSError("device unavailable")
                write(name, attribute, value)

            with patch.object(indicator, "_write", side_effect=fail_once):
                indicator.show("voice")
            self.assertIn("device unavailable", indicator.error)
            self.assert_restored(leds)

    def test_invalid_recovery_file_cannot_redirect_sysfs_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            leds = self.setup_leds(directory)
            state = Path(directory) / "state"
            state.mkdir()
            journal = state / "logo-original.json"
            journal.write_text(
                json.dumps({"../outside": {"brightness": 255, "trigger": "none"}})
            )
            indicator = LogoIndicator(state, leds)
            indicator.show("voice")
            self.assertIn("Invalid logo recovery channels", indicator.error)
            self.assertTrue(journal.exists())
            self.assertEqual((leds / LOGO[0] / "brightness").read_text(), "5")
