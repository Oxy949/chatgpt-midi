"""Run shell entry points against inert services; never chroot or signal hardware."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("sh")
if SHELL is None and os.name == "nt":
    candidate = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
    )
    if candidate.exists():
        SHELL = str(candidate)


@unittest.skipUnless(SHELL, "POSIX shell required")
class ScriptTests(unittest.TestCase):
    def test_all_shell_syntax(self):
        for script in (
            *ROOT.joinpath("scripts").rglob("*.sh"),
            ROOT / "scripts/midi-chromium",
            ROOT / "scripts-collect-midi.sh",
        ):
            with self.subTest(script=script.name):
                self.assertNotIn(
                    b"\r", script.read_bytes(), "Shell scripts require LF line endings"
                )
                subprocess.run(
                    [SHELL, "-n", script.as_posix()], check=True, capture_output=True
                )

    def run_entry(self, name, **environment):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            library = scripts / "lib"
            library.mkdir(parents=True)
            for entry in (
                "run-voice.sh",
                "run-visible-voice.sh",
                "run-headless-voice.sh",
                "login-browser.sh",
                "login-alpine-browser.sh",
                "login-alpine-browser-native.sh",
            ):
                shutil.copyfile(ROOT / "scripts" / entry, scripts / entry)
            (library / "session.sh").write_text(
                """
session_init() { echo INIT; }
session_start() { echo "START $* DISPLAY=${DISPLAY-unset}"; STARTED_PID=1; }
session_watch() { echo "WATCH $1"; }
""",
                encoding="utf-8",
            )
            (library / "display.sh").write_text(
                """
start_display() { echo "DISPLAY $*"; export DISPLAY=:99; }
start_login_browser() { echo BROWSER; STARTED_PID=1; }
""",
                encoding="utf-8",
            )
            (library / "audio.sh").write_text(
                "start_audio() { echo AUDIO; }\n", encoding="utf-8"
            )
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("MIDI_")
            }
            env.update(environment)
            result = subprocess.run(
                [SHELL, (scripts / name).as_posix()],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_both_native_login_entry_points_skip_audio(self):
        for name, env in (
            ("login-alpine-browser.sh", {"MIDI_LOGIN_NO_AUDIO": "1"}),
            ("login-alpine-browser-native.sh", {}),
        ):
            output = self.run_entry(name, **env)
            self.assertIn("BROWSER", output)
            self.assertIn("DISPLAY 800x600x24 1", output)
            self.assertNotIn("\nAUDIO\n", output)

    def test_login_starts_audio_before_browser(self):
        output = self.run_entry("login-alpine-browser.sh")
        self.assertLess(output.index("AUDIO"), output.index("BROWSER"))

    def test_visible_voice_preserves_debug_vnc_setting(self):
        output = self.run_entry("run-visible-voice.sh", MIDI_VOICE_DEBUG_VNC="1")
        self.assertIn("DISPLAY 800x600x24 1", output)
        self.assertIn("START controller python3 -m midivoice serve DISPLAY=:99", output)

    def test_headless_voice_unsets_display_and_runs_no_gui(self):
        output = self.run_entry(
            "run-headless-voice.sh", DISPLAY=":1", WAYLAND_DISPLAY="wayland-0"
        )
        self.assertNotIn("DISPLAY 800", output)
        self.assertIn(
            "START controller python3 -m midivoice serve DISPLAY=unset", output
        )
        self.assertIn("AUDIO", output)

    def test_voice_session_supervises_alice_adapter_and_can_disable_it(self):
        output = self.run_entry("run-visible-voice.sh")
        self.assertIn(
            "START quasar python3 -m midivoice quasar-listen --dispatch --cancel-stock",
            output,
        )
        self.assertNotIn(
            "START quasar", self.run_entry("run-visible-voice.sh", MIDI_QUASAR="0")
        )

    def test_live_pulse_guard_runs_before_fifo_preparation(self):
        script = """
. "$PROJECT/scripts/lib/audio.sh"
ROOT=$TEST_ROOT
python3() { echo "$*" >> "$CALLS"; return 0; }
start_audio
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usr/bin").mkdir(parents=True)
            for binary in ("pulseaudio", "sox"):
                path = root / "usr/bin" / binary
                path.write_text("#!/bin/sh\n", encoding="utf-8")
                path.chmod(0o755)
            calls = root / "calls"
            env = {
                **os.environ,
                "PROJECT": ROOT.as_posix(),
                "TEST_ROOT": root.as_posix(),
                "CALLS": calls.as_posix(),
            }
            result = subprocess.run(
                [SHELL, "-c", script], env=env, capture_output=True, timeout=5
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("pactl info", calls.read_text(encoding="utf-8"))
            self.assertNotIn("prepare", calls.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "POSIX process signals")
    def test_supervisor_detects_failed_service_and_cleans_children(self):
        script = """
set -eu
. "$PROJECT/scripts/lib/session.sh"
SESSION_PIDS=
SESSION_MAIN_PID=
LOGDIR=$TEST_ROOT
trap session_cleanup EXIT
session_start failed sh -c 'exit 7'
session_start controller sleep 30
session_watch "$STARTED_PID"
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [SHELL, "-c", script],
                timeout=8,
                capture_output=True,
                env={**os.environ, "PROJECT": ROOT.as_posix(), "TEST_ROOT": directory},
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"Session service", result.stderr)
