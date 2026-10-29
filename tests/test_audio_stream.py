from array import array
import math
import struct
import unittest
from midivoice.audio_stream import Mono16k, TARGET_RATE, SAMPLE_RATE, lowpass_taps


def four_channel_sine(seconds, freq, amplitude=600, channel=1):
    ints = array("h")
    for i in range(int(SAMPLE_RATE * seconds)):
        for c in range(4):
            ints.append(
                round(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
                if c == channel
                else 0
            )
    return ints.tobytes()


class AudioStreamTests(unittest.TestCase):
    def test_unity_gain(self):
        self.assertAlmostEqual(sum(lowpass_taps()), 1, places=6)

    def test_4ch_to_16k_1ch(self):
        c = Mono16k(channel=1, gain_db=0)
        src = four_channel_sine(0.03, 2000)
        res = b"".join(c.process(src[i : i + 7680]) for i in range(0, len(src), 7680))
        self.assertEqual(len(res), 0.03 * TARGET_RATE * 2)
        self.assertEqual(c.clipped, 0)
        self.assertTrue(any(v for (v,) in struct.iter_unpack("<h", res)))

    def test_anti_alias(self):
        def output_rms(hz):
            p = Mono16k(channel=1, gain_db=0)
            data = four_channel_sine(0.1, hz)
            dst = p.process(data)
            samples = [v for (v,) in struct.iter_unpack("<h", dst)][300:]
            return math.sqrt(sum(v * v for v in samples) / len(samples))

        self.assertGreater(output_rms(2000), output_rms(20000) * 25)

    def test_gain_and_clipping(self):
        p = Mono16k(channel=0, gain_db=36)
        src = array("h", [32767, 0, 0, 0] * 300).tobytes()
        out = p.process(src)
        self.assertEqual(len(out), 100)
        self.assertGreater(p.clipped, 0)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            Mono16k(channel=4)
        with self.assertRaises(ValueError):
            Mono16k(gain_db=50)
        with self.assertRaises(ValueError):
            Mono16k().process(b"123")


if __name__ == "__main__":
    unittest.main()
