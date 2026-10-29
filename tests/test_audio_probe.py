from array import array
import unittest
from midivoice.audio_probe import analyze_pcm


class AudioProbeTests(unittest.TestCase):
    def test_interleaved_four_channels(self):
        pcm = array("h", [100, -1000, 0, 32767] * 10).tobytes()
        result = analyze_pcm(pcm)
        self.assertEqual(result["frames"], 10)
        self.assertEqual([d["peak"] for d in result["channels"]], [100, 1000, 0, 32767])
        self.assertEqual(result["channels"][2]["rms"], 0)
        self.assertIsNone(result["channels"][2]["rms_dbfs"])
        self.assertEqual(result["channels"][3]["clipped_samples"], 10)

    def test_bad_frame_rejected(self):
        with self.assertRaises(ValueError):
            analyze_pcm(b"\0" * 3)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            analyze_pcm(b"")

    def test_rms_dbfs(self):
        result = analyze_pcm(array("h", [1024, 1024, 1024, 1024] * 2).tobytes())
        self.assertAlmostEqual(result["channels"][0]["rms_dbfs"], -30.1, delta=0.1)


if __name__ == "__main__":
    unittest.main()
