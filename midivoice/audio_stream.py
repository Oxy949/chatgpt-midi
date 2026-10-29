"""Bounded Station Midi PDM -> 16 kHz mono PCM adapter (stdlib-only).

No Quasar hooks, no ALSA reconfiguration, no system modifications.
The FIR is a simple reference adapter, not beamforming / AEC / VQE.
"""

from __future__ import annotations

from array import array
import math
from pathlib import Path
import sys
import wave

from .audio_format import (
    MIC_CHANNELS as CHANNELS,
    MIC_RATE as SAMPLE_RATE,
    MIC_DEVICE,
    VOICE_RATE,
)

DECIMATION = 6
TARGET_RATE = VOICE_RATE
TAPS = 61
CUTOFF_HZ = 6500
FRAME_BYTES = CHANNELS * 2


def lowpass_taps(n: int = TAPS, cutoff_hz: int = CUTOFF_HZ):
    """Windowed-sinc low-pass; ~6.5kHz voice band and anti-alias at 16kHz."""
    if n < 3 or n % 2 != 1 or not 0 < cutoff_hz < TARGET_RATE / 2:
        raise ValueError("Invalid FIR parameters")
    f = cutoff_hz / SAMPLE_RATE
    mid = (n - 1) // 2
    coeffs = []
    for i in range(n):
        x = i - mid
        h = 2 * f if x == 0 else math.sin(2 * math.pi * f * x) / (math.pi * x)
        window = 0.54 - 0.46 * math.cos(2 * math.pi * i / (n - 1))
        coeffs.append(h * window)
    norm = sum(coeffs)
    return tuple(v / norm for v in coeffs)


class Mono16k:
    """Streaming FIR decimator with preserved history across 10ms input blocks."""

    def __init__(self, channel: int = 1, gain_db: float = 24.0):
        if channel not in range(CHANNELS):
            raise ValueError("Channel must be between 0 and 3")
        if not -12 <= gain_db <= 36:
            raise ValueError("Gain must be between -12 and +36 dB")
        if sys.byteorder != "little":
            raise RuntimeError("This adapter expects little-endian samples")
        self.channel = channel
        self.gain = 10 ** (gain_db / 20)
        self.taps = lowpass_taps()
        self.reversed_taps = self.taps[::-1]
        self.history = [0] * len(self.taps)
        self.cursor = 0
        self.frames_received = 0
        self.clipped = 0

    def process(self, interleaved: bytes) -> bytes:
        if len(interleaved) % FRAME_BYTES:
            raise ValueError("Incomplete 4-channel S16_LE audio frame")
        src = array("h")
        src.frombytes(interleaved)
        dst = array("h")
        size = len(self.taps)
        for v in src[self.channel :: CHANNELS]:
            self.history[self.cursor] = v
            self.cursor = (self.cursor + 1) % size
            if self.frames_received % DECIMATION == DECIMATION - 1:
                idx = (self.cursor - 1) % size
                sample = 0.0
                for tap in self.reversed_taps:
                    sample += self.history[idx] * tap
                    idx = (idx - 1) % size
                value = round(sample * self.gain)
                if value > 32767:
                    value = 32767
                    self.clipped += 1
                elif value < -32768:
                    value = -32768
                    self.clipped += 1
                dst.append(value)
            self.frames_received += 1
        return dst.tobytes()


def write_voice_sample(
    seconds: int = 3,
    channel: int = 1,
    gain_db: float = 24,
    output: str = "/data/midi-voice-16k.wav",
) -> dict:
    """Explicit local diagnostic recording (saved only when invoked)."""
    import shutil
    import subprocess

    if not 1 <= seconds <= 5:
        raise ValueError("Duration must be 1..5 seconds")
    if not shutil.which("arecord"):
        raise RuntimeError("arecord not found")
    path = Path(output)
    if not path.is_absolute() or path.exists():
        raise ValueError("Specify an absolute output path that does not exist")
    if not path.parent.is_dir():
        raise ValueError("Output directory does not exist")
    path = path.resolve(strict=False)
    command = [
        "arecord",
        "-q",
        "-D",
        MIC_DEVICE,
        "-f",
        "S16_LE",
        "-c",
        str(CHANNELS),
        "-r",
        str(SAMPLE_RATE),
        "-d",
        str(seconds),
        "-t",
        "raw",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=seconds + 7)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ALSA capture timed out") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            "ALSA capture failed: " + proc.stderr.decode(errors="replace")[-800:]
        )
    converter = Mono16k(channel=channel, gain_db=gain_db)
    chunks = []
    block = SAMPLE_RATE // 100 * FRAME_BYTES
    for i in range(0, len(proc.stdout), block):
        chunks.append(converter.process(proc.stdout[i : i + block]))
    pcm = b"".join(chunks)
    if len(pcm) != seconds * TARGET_RATE * 2:
        raise RuntimeError(f"Unexpected output length: {len(pcm)} bytes")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(TARGET_RATE)
        wav.writeframes(pcm)
    path.chmod(0o600)
    return {
        "output": str(path),
        "format": "PCM S16_LE mono WAV",
        "rate": TARGET_RATE,
        "seconds": seconds,
        "channel": channel,
        "gain_db": gain_db,
        "bytes": path.stat().st_size,
        "clipped_samples": converter.clipped,
        "uploaded": False,
        "chatgpt_connected": False,
    }
