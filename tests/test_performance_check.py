import unittest
from midivoice.performance_check import NAMES
from midivoice.config import alpine_root
from shutil import which


class ResourceTests(unittest.TestCase):
    def test_processes_we_want_to_watch(self):
        for k in ("sox", "chromium", "x11vnc", "Xvfb", "python3", "pulseaudio"):
            self.assertIn(k, NAMES)


class NativeAudioIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        which("sox") or (alpine_root() / "usr/bin/sox").is_file(),
        "host sox not installed; Alpine runtime test on Midi",
    )
    def test_sox_pcm_conversion_no_network(self):
        import array
        import subprocess
        from midivoice.pulse_mic import sox_command
        from midivoice.audio_stream import SAMPLE_RATE, TARGET_RATE

        src = array.array("h", [0, 300, 0, 0] * SAMPLE_RATE).tobytes()
        command = ["sox"] + sox_command()[4:] if which("sox") else sox_command()
        proc = subprocess.run(
            command,
            input=src,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
        self.assertEqual(len(proc.stdout), TARGET_RATE * 2)
        out = array.array("h")
        out.frombytes(proc.stdout)
        self.assertTrue(any(abs(sample) > 1000 for sample in out[200:]))
