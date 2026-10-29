from contextlib import redirect_stdout, contextmanager
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from midivoice.config import Settings
from midivoice.maintenance import browser_probe


class BrowserProbeTests(unittest.TestCase):
    def test_probe_uses_and_removes_a_separate_profile(self):
        settings = Settings(state_dir=Path("/state"), profile_dir=Path("/account"))
        profiles = []

        @contextmanager
        def run(command, **kwargs):
            profile = Path(
                next(
                    flag.partition("=")[2]
                    for flag in command
                    if flag.startswith("--user-data-dir=")
                )
            )
            self.assertTrue(profile.is_dir())
            self.assertNotEqual(profile, settings.profile_dir)
            profiles.append(profile)
            self.assertEqual(command[-1], "about:blank")
            yield SimpleNamespace(
                returncode=0, communicate=lambda **kwargs: ("<html></html>", "")
            )

        output = io.StringIO()
        with (
            patch(
                "midivoice.maintenance.shutil.which", return_value="/usr/bin/chromium"
            ),
            patch("midivoice.maintenance.managed_process", side_effect=run),
            redirect_stdout(output),
        ):
            self.assertEqual(browser_probe(settings), 0)
        self.assertFalse(profiles[0].exists())
        self.assertTrue(json.loads(output.getvalue())["temporary_profile"])
