import asyncio
import struct
import unittest
from unittest.mock import patch

from midivoice.speaker import SAMPLE_RATE, FRAME_BYTES, make_cue, speaker_test
from midivoice.mic_server import INPUT_10MS, OUTPUT_10MS, MAX_QUEUED, MicrophoneCapture


class SpeakerTests(unittest.TestCase):
    def test_safe_cue(self):
        data = make_cue()
        self.assertEqual(len(data), int(0.4 * SAMPLE_RATE * FRAME_BYTES))
        samples = struct.unpack("<" + "h" * (len(data) // 2), data)
        self.assertGreater(max(samples), 0)
        self.assertLessEqual(max(abs(x) for x in samples), 830)
        self.assertEqual(samples[::2], samples[1::2])

    def test_speaker_command_uses_alsa_default(self):
        class Fake:
            returncode = 0
            stderr = b""

        with (
            patch("midivoice.speaker.shutil.which", return_value="/usr/bin/aplay"),
            patch("midivoice.speaker.subprocess.run", return_value=Fake()) as r,
        ):
            result = speaker_test()
        self.assertTrue(result["ok"])
        args, kwargs = r.call_args
        self.assertEqual(args[0][args[0].index("-D") + 1], "default")
        self.assertEqual(kwargs["timeout"], 8)

    def test_bad_playback_exit(self):
        class Fake:
            returncode = 1
            stderr = b"failure"

        with (
            patch("midivoice.speaker.shutil.which", return_value="/usr/bin/aplay"),
            patch("midivoice.speaker.subprocess.run", return_value=Fake()),
        ):
            with self.assertRaisesRegex(RuntimeError, "failure"):
                speaker_test()

    def test_microphone_frame_sizes(self):
        self.assertEqual(INPUT_10MS, 7680)
        self.assertEqual(OUTPUT_10MS, 320)
        self.assertEqual(MAX_QUEUED, 6)
        from midivoice.audio_stream import Mono16k

        self.assertEqual(len(Mono16k().process(b"\0" * INPUT_10MS)), OUTPUT_10MS)

    def test_microphone_queue_latest_framing(self):
        async def run():
            capture = MicrophoneCapture()
            for i in range(MAX_QUEUED + 2):
                if capture.queue.full():
                    capture.queue.get_nowait()
                    capture.dropped_frames += 1
                capture.queue.put_nowait(bytes([i]) * OUTPUT_10MS)
            self.assertEqual(capture.dropped_frames, 2)
            self.assertEqual((await capture.next_frame())[0], 2)

        asyncio.run(run())
