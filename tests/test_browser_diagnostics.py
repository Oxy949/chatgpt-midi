"""Snapshot, IP check and extension info without a real logged-in browser."""

import asyncio
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from midivoice import browser, browser_diagnostics as d, controller as cli


class BrowserDiagnosticUnitTests(unittest.TestCase):
    def test_snapshot_writes_private_files_and_no_cookie_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snap = d.write_snapshot(
                root / "diagnostics",
                {"buttons": 0},
                "<html>Private conversation</html>",
                b"PNG",
            )
            self.assertEqual(
                Path(snap["html"]).read_text(encoding="utf-8"),
                "<html>Private conversation</html>",
            )
            self.assertEqual(Path(snap["png"]).read_bytes(), b"PNG")
            if os.name == "posix":
                self.assertEqual(Path(snap["folder"]).stat().st_mode & 0o777, 0o700)
            for file in ("page.html", "screen.png", "structure.json"):
                if os.name == "posix":
                    self.assertEqual(
                        (Path(snap["folder"]) / file).stat().st_mode & 0o777, 0o600
                    )
            self.assertEqual(
                json.loads(
                    (Path(snap["folder"]) / "structure.json").read_text(
                        encoding="utf-8"
                    )
                ),
                {"buttons": 0},
            )

    def test_extension_status_uses_pref_and_target_but_does_not_claim_vpn(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            identifier = "abcdefghijklmnopabcdefghijklmnop"
            extension = profile / "Default" / "Extensions" / identifier / "1.0"
            extension.mkdir(parents=True)
            (extension / "manifest.json").write_text(
                '{"name":"My VPN", "version":"1.0"}'
            )
            (profile / "Default" / "Preferences").write_text(
                json.dumps({"extensions": {"settings": {identifier: {"state": 1}}}})
            )
            items = d.extension_inventory(profile)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["name"], "My VPN")
            self.assertTrue(items[0]["enabled_in_saved_preferences"])
            targets = d.active_extension_targets(
                [
                    {
                        "url": "chrome-extension://" + identifier + "/background.js",
                        "type": "service_worker",
                    }
                ]
            )
            self.assertEqual(targets[0]["target_type"], "service_worker")
            self.assertEqual(
                d.decode_ip_response('{"ip":"203.0.113.12"}'), "203.0.113.12"
            )
            self.assertIsNone(d.decode_ip_response('{"ip":"token"}'))

    def test_extension_without_explicit_state_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            identifier = "abcdefghijklmnopabcdefghijklmnop"
            extension = profile / "Default" / "Extensions" / identifier / "1.0"
            extension.mkdir(parents=True)
            (extension / "manifest.json").write_text('{"name":"VPN"}')
            preferences = profile / "Default" / "Preferences"
            for state, expected in ((None, None), (0, False), (1, True), ("1", None)):
                with self.subTest(state=state):
                    settings = {} if state is None else {"state": state}
                    preferences.write_text(
                        json.dumps({"extensions": {"settings": {identifier: settings}}})
                    )
                    self.assertIs(
                        d.extension_inventory(profile)[0][
                            "enabled_in_saved_preferences"
                        ],
                        expected,
                    )

    def test_flags_carry_extension_setting_from_daemon_environment(self):
        from midivoice.browser_launch import low_resource_chromium_flags

        with patch.dict(os.environ, {"MIDI_HEADLESS_EXTENSIONS": "1"}):
            self.assertNotIn("--disable-extensions", low_resource_chromium_flags())
        with patch.dict(os.environ, {"MIDI_HEADLESS_EXTENSIONS": "0"}):
            self.assertIn("--disable-extensions", low_resource_chromium_flags())


class DummyProcess:
    def poll(self):
        return None


class ConcurrentDebugTests(unittest.IsolatedAsyncioTestCase):
    async def test_minimize_waits_for_the_window_manager(self):
        b = browser.Chromium()
        b.target_id = "page"
        b.send = AsyncMock(side_effect=[{"windowId": 1}, {}])
        b.evaluate = AsyncMock(side_effect=["visible", "hidden"])
        with patch("midivoice.browser.asyncio.sleep", new=AsyncMock()):
            await b.set_ui_visible(False)
        self.assertTrue(b.ui_hidden)
        self.assertEqual(b.evaluate.await_count, 2)

    async def test_snapshot_bypasses_voice_lock(self):
        fake = type("FakeBrowser", (), {})()
        fake.proc = DummyProcess()
        fake.running = True
        fake.snapshot = AsyncMock(return_value={"png": "/tmp/page.png"})
        ctl = cli.Controller(browser=fake)
        await ctl.lock.acquire()
        try:
            result = await asyncio.wait_for(ctl.handle("snapshot"), timeout=1)
            self.assertTrue(result["ok"])
            self.assertEqual(result["snapshot"]["png"], "/tmp/page.png")
        finally:
            ctl.lock.release()
        fake.snapshot.assert_awaited_once()

    async def test_network_cdp_uses_no_cookies_and_does_not_claim_vpn(self):
        b = browser.Chromium()
        b.session = "session1"
        b.send = AsyncMock(
            side_effect=[
                {"frameTree": {"frame": {"id": "a"}}},
                {
                    "resource": {
                        "success": True,
                        "httpStatusCode": 200,
                        "stream": "handle1",
                    }
                },
                {"data": '{"ip":"203.0.113.40"}', "eof": True},
                {},
            ]
        )
        result = await b.network_check()
        self.assertEqual(result["browser_egress_ip"], "203.0.113.40")
        self.assertFalse(result["vpn_confirmed"])
        self.assertEqual(
            b.send.await_args_list[1].args[1]["options"]["includeCredentials"], False
        )
        self.assertEqual(
            b.send.await_args_list[1].args[1]["url"],
            "https://api.ipify.org?format=json",
        )

    async def test_snapshot_grabs_existing_page_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = browser.Chromium()
            b.connection.proc = DummyProcess()
            b.session = "session1"
            b.evaluate = AsyncMock(side_effect=[{"buttons": 0}, "<html>Hello</html>"])
            b.send = AsyncMock(return_value={"data": base64.b64encode(b"PNG").decode()})
            b.extension_status = AsyncMock(return_value={"installed": []})
            from dataclasses import replace

            b.settings = replace(b.settings, state_dir=Path(tmp))
            output = await b.snapshot()
            self.assertTrue(Path(output["png"]).is_file())
            self.assertEqual(
                Path(output["html"]).read_text(encoding="utf-8"), "<html>Hello</html>"
            )
            self.assertEqual(b.send.await_args.args[0], "Page.captureScreenshot")


if __name__ == "__main__":
    unittest.main()
