from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.device import Device


class DeviceToolTests(unittest.TestCase):
    def test_legacy_adb_exit_code_and_script_quoting(self):
        device = Device("station:5555")
        calls = []
        script = 'printf "%s\\n" "quoted value | not a pipeline"\nexit 7'

        def command(*args, **kwargs):
            calls.append(args)
            if args[0] == "push":
                uploaded = Path(args[1]).read_bytes()
                self.assertNotIn(b"\r", uploaded)
                self.assertIn(script.encode(), uploaded)
                return SimpleNamespace(returncode=0, stderr=b"")
            return SimpleNamespace(
                returncode=0,
                stdout=b"output\r\r\n__MIDIVOICE_EXIT_fixed__=7\r\r\n",
                stderr=b"",
            )

        with (
            patch("tools.device.uuid4", return_value=SimpleNamespace(hex="fixed")),
            patch.object(device, "command", side_effect=command),
        ):
            result = device.run(script)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.output, "output\n")
        self.assertEqual(calls[-1], ("shell", "sh", "/tmp/midivoice-dev-fixed.sh"))

    def test_deployment_rejects_unrelated_or_traversing_paths(self):
        device = Device()
        for path in (
            "/system",
            "/data/../system",
            "/data/midi-chatgpt-voice-name/../../system",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                device.deploy(path)
