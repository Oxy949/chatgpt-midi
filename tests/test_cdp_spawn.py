"""CDP pipe regression tests for both direct and Alpine process startup."""

import asyncio
import os
import sys
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from midivoice.cdp_spawn import CHILD_BOOTSTRAP, build_cdp_launch
from midivoice import browser

MOCK = r"""#!/usr/bin/env python3
import json,os,sys
buf=b''
while True:
    part=os.read(3,4096)
    if not part: break
    buf+=part
    while b'\0' in buf:
        raw,buf=buf.split(b'\0',1)
        data=json.loads(raw)
        method=data['method']
        if method=='Target.getTargets':
            result={'targetInfos':[{'type':'page','targetId':'one','url':'https://chatgpt.com/'}]}
        elif method=='Target.attachToTarget': result={'sessionId':'s'}
        elif method=='Runtime.evaluate': result={'result':{'value':'https://chatgpt.com/'}}
        else: result={}
        os.write(4,json.dumps({'id':data['id'],'result':result}).encode()+b'\0')
        if method=='Browser.close': sys.exit(0)
"""


class CDPSpawnTests(unittest.TestCase):
    def test_bootstrap_executes_both_branches_without_importing_project(self):
        for root in ("-", "/fake-alpine"):
            with self.subTest(root=root), ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        sys, "argv", ["bootstrap", "10", "11", root, "/fake", "--flag"]
                    )
                )
                stack.enter_context(
                    patch.dict(
                        os.environ, {"DISPLAY": ":99", "LD_PRELOAD": "host-only"}
                    )
                )
                stack.enter_context(patch("os.dup", side_effect=[100, 101]))
                for name in (
                    "dup2",
                    "set_inheritable",
                    "close",
                    "write",
                    "chroot",
                    "chdir",
                    "setgroups",
                    "setgid",
                    "setuid",
                ):
                    stack.enter_context(patch("os." + name, create=True))
                execute = stack.enter_context(patch("os.execve"))
                exec(CHILD_BOOTSTRAP, {})
                binary, flags, env = execute.call_args.args
                self.assertEqual(binary, "/fake")
                self.assertEqual(flags, ["/fake", "--flag"])
                self.assertEqual(env["DISPLAY"], ":99")
                if root != "-":
                    self.assertNotIn("LD_PRELOAD", env)
                    self.assertEqual(env["PULSE_SOURCE"], "midi_microphone")

    def test_station_args_reject_unprivileged_or_missing_chromium(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            with patch.dict(os.environ, {"MIDI_ALPINE_ROOT": d}):
                with self.assertRaises(RuntimeError):
                    build_cdp_launch(
                        "/app/scripts/midi-chromium", [], 10, 11, p / "profile"
                    )

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor inheritance")
    def test_child_direct_handoff_across_exec(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "chromium-mock"
            fake.write_text(MOCK)
            fake.chmod(0o755)

            async def run():
                c = browser.Chromium(str(fake))
                try:
                    await c.start("https://chatgpt.com/")
                    self.assertEqual(await c.current_url(), "https://chatgpt.com/")
                finally:
                    await c.stop()

            from midivoice.config import Settings

            settings = Settings(
                state_dir=Path(d) / "state", profile_dir=Path(d) / "profile"
            )
            with patch.object(browser.Settings, "from_env", return_value=settings):
                asyncio.run(run())

    def test_isolated_bootstrap_and_diagnostic_stages(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "chromium-mock"
            fake.write_text(MOCK)
            fake.chmod(0o755)
            cmd, env, args = build_cdp_launch(str(fake), [], 9, 10, Path(d) / "profile")
            self.assertEqual(cmd[1:3], ["-I", "-S"])
            self.assertIn("Python bootstrap entered", cmd[4])
            self.assertIn("Alpine chroot and uid1000 ready", cmd[4])

    def test_no_sandbox_disabled(self):
        self.assertNotIn("--no-sandbox", CHILD_BOOTSTRAP)
        self.assertNotIn("--remote-debugging-port", CHILD_BOOTSTRAP)


if __name__ == "__main__":
    unittest.main()
