from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import unittest
import math
import struct
import subprocess
from unittest.mock import patch

from midivoice import pulse_speaker, pulse
from midivoice import alpine_exec


class TestDuplexAudio(unittest.TestCase):
    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(os, "geteuid")
        and os.geteuid() == 0
        and (alpine_exec.alpine_root() / "usr/bin/sox").is_file(),
        "Native Alpine SoX required",
    )
    def test_native_playback_keeps_duration_and_duplicates_channels(self):
        source = b"".join(
            struct.pack("<hh", round(1000 * math.sin(2 * math.pi * 440 * i / 48000)), 0)
            for i in range(12000)
        )
        result = subprocess.run(
            pulse_speaker.sox_command(), input=source, capture_output=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(len(result.stdout), len(source) * 4)
        frames = list(struct.iter_unpack("<hhhh", result.stdout))
        self.assertTrue(
            all(
                left == left2 and right == right2 == 0
                for left, right, left2, right2 in frames
            )
        )
        self.assertGreater(max(abs(frame[0]) for frame in frames), 950)
        self.assertLess(max(abs(frame[0]) for frame in frames), 1050)

    @unittest.skipUnless(os.name == "posix", "Linux pipe capacity")
    def test_playback_queue_cannot_hide_hundreds_of_milliseconds(self):
        import fcntl

        if not hasattr(fcntl, "F_SETPIPE_SZ"):
            self.skipTest("Linux F_SETPIPE_SZ required")
        reader, writer = os.pipe()
        try:
            capacity = pulse_speaker.bound_playback_pipe(writer)
            self.assertLessEqual(capacity / (48000 * 4), 0.05)
            os.set_blocking(writer, False)
            written = 0
            while True:
                try:
                    written += os.write(writer, b"\0" * 1024)
                except BlockingIOError:
                    break
            self.assertEqual(written, capacity)
            self.assertEqual(len(os.read(reader, capacity)), capacity)
        finally:
            os.close(reader)
            os.close(writer)

    def root(self, d):
        root = Path(d)
        (root / "tmp").mkdir()
        (root / "sbin").mkdir()
        (root / "sbin/apk").touch()
        return root

    @patch("midivoice.pulse.os.chown", create=True)
    def test_pulse_config_and_setup_both_endpoints(self, _chown):
        with tempfile.TemporaryDirectory() as d:
            root = self.root(d)
            info = pulse.setup(root)
            config = (root / "home/chatgpt/midi-pulse.pa").read_text(encoding="utf-8")
            self.assertIn("module-pipe-source", config)
            self.assertIn("module-pipe-sink", config)
            self.assertNotIn("module-null-sink", config)
            self.assertIn("rate=48000 channels=2", config)
            self.assertIn("set-default-sink midi_speaker", config)
            self.assertIn("midi_speaker", info["sink"])
            self.assertFalse(pulse.speaker_fifo_path(root).exists())
            self.assertFalse(pulse.fifo_path(root).exists())

    def test_speaker_fifo_format_and_command(self):
        self.assertEqual(pulse_speaker.CHUNK_SIZE, 9600)
        self.assertEqual(pulse_speaker.SAMPLE_RATE, 48000)
        self.assertEqual(pulse_speaker.FRAME_BYTES, 4)
        with patch("midivoice.speaker.shutil.which", return_value="/usr/bin/aplay"):
            self.assertEqual(
                pulse_speaker.aplay_command(),
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
                    "--start-delay=20000",
                ],
            )

    def test_invalid_speaker_fifo(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.root(d)
            path = pulse.speaker_fifo_path(root)
            with self.assertRaises(FileNotFoundError):
                pulse_speaker.validate_sink_fifo(path, root)
            path.parent.mkdir()
            path.write_text("not a fifo")
            with self.assertRaisesRegex(RuntimeError, "ownership/type invalid"):
                pulse_speaker.validate_sink_fifo(path, root)

    @patch("midivoice.pulse.os.chown", create=True)
    @unittest.skipUnless(os.name == "posix", "POSIX FIFOs")
    def test_stale_and_secure_both_fifos(self, _chown):
        with tempfile.TemporaryDirectory() as d:
            root = self.root(d)
            pulse.setup(root)
            for fifo in (pulse.fifo_path(root), pulse.speaker_fifo_path(root)):
                os.mkfifo(fifo)
            real_lstat = Path.lstat

            def fake_lstat(path):
                st = real_lstat(path)
                return SimpleNamespace(st_mode=st.st_mode, st_uid=1000)

            with patch.object(Path, "lstat", fake_lstat):
                pulse.secure_fifo(root)
                self.assertTrue(pulse.clear_stale_fifo(root))
            self.assertFalse(pulse.fifo_path(root).exists())
            self.assertFalse(pulse.speaker_fifo_path(root).exists())

    def test_write_all_handles_partial_write(self):
        output = bytearray()

        def partially(fd, mv):
            n = min(len(mv), 3)
            output.extend(bytes(mv[:n]))
            return n

        with patch("midivoice.pulse_speaker.os.write", partially):
            pulse_speaker.write_all(1, b"abcdefg")
        self.assertEqual(bytes(output), b"abcdefg")

    def test_cue_and_pacat(self):
        cue = pulse_speaker.cue_bytes()
        self.assertGreater(len(cue), 40000)
        self.assertEqual(len(cue) % 4, 0)
        # Listening safety: generated cue does not exceed 10% full-scale.
        import struct

        self.assertLessEqual(
            max(abs(v[0]) for v in struct.iter_unpack("<h", cue)), 3277
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/bin/pacat").touch()
            with patch("midivoice.pulse_speaker.subprocess.run") as runner:
                runner.return_value = SimpleNamespace(returncode=0)
                pulse_speaker.test_tone(root)
                args, kwargs = runner.call_args
                self.assertIn("pacat", args[0])
                self.assertIn("--device=midi_speaker", args[0])
                self.assertEqual(kwargs["input"], cue)

    def test_no_network_audio_transfer(self):
        cfg = pulse.pulse_config()
        self.assertNotIn("module-native-protocol-tcp", cfg)
        self.assertNotIn("auth-anonymous", cfg)
        self.assertNotIn("load-module module-alsa-sink", cfg)
        self.assertEqual(alpine_exec.alpine_root().name, "alpine")


if __name__ == "__main__":
    unittest.main()
