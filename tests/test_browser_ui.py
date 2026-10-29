"""Render local UI fixtures in a fresh Chromium profile, never an account profile."""

import html
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from midivoice.browser_ui import voice_inspector_script
from midivoice.config import alpine_root
from midivoice.processes import managed_process
from midivoice.webrtc_diagnostics import RTC_STATS_JS
from midivoice.navigation import navigation_script

ROOT = alpine_root()
STATION = (
    os.name == "posix"
    and os.geteuid() == 0
    and (ROOT / "usr/bin/chromium").is_file()
    and (ROOT / "proc/self/status").is_file()
)
DESKTOP = shutil.which("chromium") if os.name == "posix" and os.geteuid() != 0 else None


@unittest.skipUnless(
    STATION or DESKTOP, "Chromium with a working POSIX sandbox is required"
)
class BrowserUITests(unittest.TestCase):
    def inspect(self, body, *, title="ChatGPT", script=None):
        directory = ROOT / "tmp" if STATION else None
        with tempfile.TemporaryDirectory(prefix="midi-ui-", dir=directory) as temporary:
            folder = Path(temporary)
            profile = folder / "profile"
            profile.mkdir(mode=0o700)
            page = folder / "fixture.html"
            markup = (
                '<!doctype html><meta charset="utf-8"><title>'
                + html.escape(title)
                + "</title><body>"
                + body
                + '<pre id="result"></pre><script>'
                + 'document.querySelector("#result").textContent=JSON.stringify('
                + (script or voice_inspector_script("start"))
                + ");</script>"
            )
            page.write_text(markup, encoding="utf-8")
            if STATION:
                os.chown(folder, 1000, 1000)
                os.chown(profile, 1000, 1000)
                os.chown(page, 1000, 1000)
                page.chmod(0o600)
                url = "file:///" + page.relative_to(ROOT).as_posix()
                profile_argument = "/" + profile.relative_to(ROOT).as_posix()
            else:
                url, profile_argument = page.as_uri(), str(profile)
            arguments = [
                DESKTOP or "/usr/bin/chromium",
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
                "--user-data-dir=" + profile_argument,
                "--dump-dom",
                url,
            ]
            if STATION:
                command = [
                    "chroot",
                    str(ROOT),
                    "/bin/busybox",
                    "su",
                    "-s",
                    "/bin/sh",
                    "chatgpt",
                    "-c",
                    shlex.join(
                        [
                            "exec",
                            "/usr/bin/env",
                            "-i",
                            "HOME=/home/chatgpt",
                            "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                            *arguments,
                        ]
                    ),
                ]
            else:
                command = arguments
            with managed_process(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            ) as process:
                stdout, stderr = process.communicate(timeout=60 if STATION else 25)
                result = subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
            self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            match = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
            self.assertIsNotNone(match, result.stderr[-1000:])
            return json.loads(html.unescape(match[1]))

    def test_rtc_stats_export_counters_without_endpoints_or_credentials(self):
        fixture = r"""<script>
          function series(values,statsType='inbound-rtp'){return {toJSON:()=>({statsType,
            startTime:'2026-10-29T00:00:00Z',endTime:'2026-10-29T00:00:02Z',
            values:JSON.stringify(values)})};}
          window.peerConnectionDataStore={old:{toJSON:()=>({url:'https://chatgpt.com/',
            stats:{'old-packetsReceived':{toJSON:()=>({statsType:'inbound-rtp',
              startTime:'2020-01-01T00:00:00Z',endTime:'2020-01-01T00:00:02Z',values:'[0,1]'})}}})},
            secretId:{toJSON:()=>({
            url:'https://chatgpt.com/c/private',rtcConfiguration:{credential:'secret'},
            stats:{'192.0.2.1-totalSamplesReceived':series([100,1100]),
              '192.0.2.1-concealedSamples':series([0,100]),
              '192.0.2.1-address':series(['secret-ip']),
              'private-totalSamplesCount':series([100,1100],'media-playout'),
              'private-totalPlayoutDelay':series([1,51],'media-playout'),
              'private-totalSamplesDuration':series([1,11],'media-playout'),
              'private-synthesizedSamplesDuration':series([0,1],'media-playout')}})}};
        </script>"""
        result = self.inspect(fixture, script=RTC_STATS_JS)
        report = result["connections"][0]["reports"][0]
        self.assertEqual(report["concealed_percent"], 10)
        self.assertGreaterEqual(report["age_s"], 0)
        playout = result["connections"][0]["reports"][1]
        self.assertEqual(playout["playout_delay_ms"], 50)
        self.assertEqual(playout["synthesized_percent"], 10)
        encoded = json.dumps(result)
        for private in ("secret", "192.0.2.1", "chatgpt.com", "credential"):
            self.assertNotIn(private, encoded)

    def test_workspace_switcher_reports_confirmed_state(self):
        result = self.inspect(
            '<button>Work</button><div role="group" aria-label="Режим редактора">'
            '<button aria-pressed="true">Чат</button>'
            '<button aria-pressed="false">Работа</button></div>',
            script=navigation_script("mode", "work"),
        )
        self.assertEqual(result["workspace_mode"], "chat")
        self.assertTrue(result["has_switcher"])
        self.assertIn("point", result)

    def test_project_row_uses_full_name_and_deduplicates_safe_links(self):
        identifier = "g-p-1234567890abcdef1234567890abcdef"
        result = self.inspect(
            "<button>Чат джипити для дома</button>"
            f'<div role="button" data-app-action-sidebar-project-id="{identifier}" '
            'data-app-action-sidebar-project-label="ChatGPT для дома">ChatGPT…</div>'
            '<a href="https://evil.example/g/g-p-1234567890abcdef/project">ChatGPT для дома</a>',
            script=navigation_script("project", "чат джипити для дома"),
        )
        self.assertEqual(result["project_matches"], 1)
        self.assertEqual(result["target_project_id"], identifier)
        self.assertIn("point", result)

    def test_start_excludes_dictation_mute_and_offscreen_buttons(self):
        page = self.inspect("""<button aria-label="Dictate voice input">Dictate</button>
            <button aria-label="Mute voice">Mute</button>
            <button aria-label="Start voice mode" style="position:absolute;top:3000px">Offscreen</button>
            <button aria-label="Start voice mode">Voice</button>""")
        self.assertTrue(page["voice_button"])
        self.assertEqual(page["voice_candidates"], 2)
        self.assertEqual(page["hidden_voice_candidates"], 1)

    def test_end_is_distinct_from_start(self):
        page = self.inspect('<button aria-label="End voice call">End</button>')
        self.assertTrue(page["end_button"])
        self.assertFalse(page["voice_button"])

    def test_intro_is_clicked_before_composer(self):
        page = self.inspect("""<button aria-label="Start voice mode" style="position:fixed;left:400px">Voice</button>
            <div role="dialog"><h2>Meet voice</h2><button>Continue</button></div>""")
        self.assertTrue(page["onboarding_button"])
        self.assertEqual(page["onboarding_stage"], "introduction")
        self.assertLess(page["point"]["x"], 200)

    def test_voice_selection(self):
        page = self.inspect(
            '<div role="dialog"><h2>Choose your voice</h2><button>Start voice</button></div>'
        )
        self.assertTrue(page["onboarding_button"])
        self.assertEqual(page["onboarding_stage"], "voice_selection")

    def test_chat_text_does_not_trigger_voice_quota(self):
        page = self.inspect("<main>Explain the voice limit message</main>")
        self.assertFalse(page["voice_limit_reached"])

    def test_visible_quota_notice_blocks_clicks(self):
        page = self.inspect(
            '<div role="alert">You have reached the voice limit</div>'
            '<button aria-label="Start voice mode">Voice</button>'
        )
        self.assertTrue(page["voice_limit_reached"])
        self.assertNotIn("point", page)

    def test_russian_daily_voice_limit_dialog_blocks_clicks(self):
        page = self.inspect(
            '<div role="dialog"><h2>Достигнут дневной лимит</h2>'
            "<p>На сегодня вы исчерпали лимит голосового общения. "
            "Лимит будет сброшен в 21:49.</p><button>Понятно</button></div>"
            '<button aria-label="Start voice mode">Voice</button>'
        )
        self.assertTrue(page["voice_limit_reached"])
        self.assertNotIn("point", page)

    def test_verification_blocks_clicks(self):
        page = self.inspect(
            '<button aria-label="Start voice mode">Voice</button>',
            title="Just a moment...",
        )
        self.assertTrue(page["verification_required"])
        self.assertNotIn("point", page)

    def test_turnstile_with_chatgpt_title_is_detected(self):
        page = self.inspect(
            '<script type="application/x-inert" src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
        )
        self.assertTrue(page["verification_required"])
