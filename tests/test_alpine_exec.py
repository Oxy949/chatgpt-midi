import unittest
from pathlib import Path
from midivoice.alpine_exec import mapped_args, ALLOWED
from midivoice.browser_launch import low_resource_chromium_flags
from unittest.mock import patch


class AlpineExecTests(unittest.TestCase):
    def test_chroot_environment_does_not_inherit_host_runtime(self):
        from midivoice.alpine_exec import alpine_environment

        env = alpine_environment(
            {
                "DISPLAY": ":99",
                "LANG": "en_US.UTF-8",
                "LD_PRELOAD": "host",
                "PYTHONHOME": "/host",
                "ALSA_CONFIG_PATH": "/host.conf",
            }
        )
        self.assertEqual(env["DISPLAY"], ":99")
        self.assertEqual(env["HOME"], "/home/chatgpt")
        for name in ("LD_PRELOAD", "PYTHONHOME", "ALSA_CONFIG_PATH"):
            self.assertNotIn(name, env)

    def test_profile_traversal_and_relative_path_refused(self):
        for profile in ("/data/opt/midi-browser/alpine/../../outside", "profile"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                mapped_args(
                    ["--user-data-dir=" + profile],
                    Path("/data/opt/midi-browser/alpine"),
                )

    def setUp(self):
        self.root = Path("/data/opt/midi-browser/alpine")

    def test_absolute_profile_maps_into_chroot(self):
        flags = mapped_args(
            [
                "--user-data-dir=/data/opt/midi-browser/alpine/home/chatgpt/profile",
                "--remote-debugging-pipe",
                "--app=https://chatgpt.com/",
            ],
            self.root,
        )
        self.assertEqual(flags[0], "--user-data-dir=/home/chatgpt/profile")
        self.assertIn("--remote-debugging-pipe", flags)

    def test_external_profile_refused(self):
        with self.assertRaises(ValueError):
            mapped_args(["--user-data-dir=/tmp/elsewhere"], self.root)

    def test_insecure_flags_refused(self):
        for arg in (
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--ignore-certificate-errors",
            "--disable-web-security",
        ):
            with self.subTest(arg=arg):
                with self.assertRaises(ValueError):
                    mapped_args([arg], self.root)

    def test_low_resource_browser_enabled(self):
        with patch.dict("os.environ", {"MIDI_BROWSER_LOW_MEMORY": "1"}):
            flags = low_resource_chromium_flags()
        self.assertIn("--disable-dev-shm-usage", flags)
        self.assertIn("--renderer-process-limit=1", flags)
        self.assertNotIn("--no-sandbox", flags)

    def test_low_resource_browser_optional(self):
        with patch.dict("os.environ", {"MIDI_BROWSER_LOW_MEMORY": "0"}):
            self.assertEqual(low_resource_chromium_flags(), [])

    def test_binaries_allowlist(self):
        self.assertEqual(
            ALLOWED,
            {
                "chromium",
                "Xvfb",
                "x11vnc",
                "openbox",
                "pulseaudio",
                "pactl",
                "sox",
                "pacat",
            },
        )


if __name__ == "__main__":
    unittest.main()
