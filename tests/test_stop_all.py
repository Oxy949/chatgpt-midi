"""Safe, offline process-matcher tests; never send signals."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stop-all.sh"
BASE = SCRIPT.parent.parent


@unittest.skipUnless(os.name == "posix", "Linux /proc process matching")
class StopAllTests(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)

    def test_scoped_process_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            root = tmp / "alpine"
            root.mkdir()
            root = root.resolve()  # /opt/tmp on Entware is itself a symlink.
            proc = tmp / "proc"
            proc.mkdir()

            def fake(pid, comm, cmd, cwd=None, process_root=None):
                p = proc / str(pid)
                p.mkdir()
                (p / "comm").write_text(comm + "\n")
                (p / "cmdline").write_bytes(
                    b"\x00".join(x.encode() for x in cmd) + b"\x00"
                )
                (p / "root").symlink_to(process_root or "/")
                (p / "cwd").symlink_to(cwd or "/")

            # Known module processes, inside Alpine or in project directory.
            fake(
                101,
                "chrome",
                ["/usr/lib/chromium/chromium", "--type=renderer"],
                process_root=root,
            )
            fake(102, "Xvfb", ["/usr/bin/Xvfb", ":99", "-noreset"], process_root=root)
            fake(
                103, "x11vnc", ["/usr/bin/x11vnc", "-display", ":99"], process_root=root
            )
            fake(
                104,
                "pulseaudio",
                ["/usr/bin/pulseaudio", "-F", "/home/chatgpt/midi-pulse.pa"],
                process_root=root,
            )
            fake(
                105,
                "sox",
                ["/usr/bin/sox", "--buffer", "2048", "-t", "raw", "-", "remix", "1"],
                process_root=root,
            )
            fake(
                106,
                "python3",
                ["/opt/bin/python3", "-m", "midivoice", "serve"],
                cwd=BASE,
            )
            fake(
                107,
                "python3",
                ["/opt/bin/python3", "-m", "midivoice.pulse_mic", "stream"],
                cwd=BASE,
            )
            fake(
                108,
                "aplay",
                [
                    "/usr/bin/aplay",
                    "-q",
                    "-D",
                    "default",
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-c",
                    "2",
                    "-r",
                    "48000",
                ],
                cwd=BASE,
            )
            fake(
                109,
                "arecord",
                ["/usr/bin/arecord", "-q", "-D", "midi_capture", "-f", "S16_LE"],
                cwd=BASE,
            )
            fake(110, "sh", ["sh", "scripts/run-visible-voice.sh"], cwd=BASE)
            fake(
                111,
                "chrome_crashpa",
                ["/usr/lib/chromium/chrome_crashpad_handler", "--monitor-self"],
                process_root=root,
            )
            # Never touch unrelated or firmware processes.
            fake(120, "bluealsa-aplay", ["/usr/bin/bluealsa-aplay", "-S"])
            fake(121, "maind", ["/usr/bin/maind"])
            fake(122, "python3", ["/opt/bin/python3", "-m", "otherapp"], cwd=BASE)
            fake(123, "Xvfb", ["/usr/bin/Xvfb", ":1", "-noreset"], process_root=root)
            fake(
                124,
                "pulseaudio",
                ["/usr/bin/pulseaudio", "--daemonize=no"],
                process_root=root,
            )
            fake(
                125,
                "chromium",
                ["/usr/bin/chromium", "--user-data-dir=/tmp/other"],
                process_root="/",
            )
            fake(126, "sh", ["sh", "scripts/run-visible-voice.sh"], cwd=tmp)
            fake(127, "arecord", ["/usr/bin/arecord", "-D", "hw:0,1"], cwd=BASE)
            fake(
                128,
                "python3",
                ["/opt/bin/python3", "-m", "midivoice", "serve"],
                cwd=tmp,
            )
            env = os.environ.copy()
            env["MIDI_ALPINE_ROOT"] = str(root)
            env["MIDI_STOP_TEST_PROC"] = str(proc)
            p = subprocess.run(
                ["sh", str(SCRIPT), "--dry-run"],
                cwd=BASE,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            lines = [
                line
                for line in p.stdout.splitlines()
                if line[:1].isdigit() and ": " in line
            ]
            found = {int(line.partition(": ")[0]) for line in lines}
            self.assertEqual(found, set(range(101, 112)), p.stdout)
            self.assertIn("DRY RUN", p.stdout)
            self.assertTrue((proc / "120").exists())


if __name__ == "__main__":
    unittest.main()
