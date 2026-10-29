"""Safe, optional ALSA 'default' playback on Station Midi.

Never opens hw:0,0 or edits the global mixer. Audio is silent except when the
user explicitly starts a playback command.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess

from .audio_format import (
    PLAYBACK_DEVICE as SINK,
    PLAYBACK_RATE as SAMPLE_RATE,
    PLAYBACK_CHANNELS as CHANNELS,
    PLAYBACK_FRAME_BYTES as FRAME_BYTES,
)


def aplay_command(
    *, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS
) -> list[str]:
    binary = shutil.which("aplay")
    if not binary:
        raise RuntimeError("aplay is not installed")
    return [
        binary,
        "-q",
        "-D",
        SINK,
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-c",
        str(channels),
        "-r",
        str(sample_rate),
        # Start after one hardware period. Waiting for the entire converted
        # dmix buffer can leave this firmware's playback PCM in PREPARED.
        "--start-delay=20000",
    ]


def make_cue() -> bytes:
    """Two gentle 120ms notes with fades at ~-32 dBFS; 48k stereo S16_LE."""
    amplitude = 0.025
    output = bytearray()
    for hz in (523.25, 659.25):
        count = int(0.12 * SAMPLE_RATE)
        fade = int(0.012 * SAMPLE_RATE)
        for i in range(count):
            ramp = min(1.0, i / fade, (count - 1 - i) / fade)
            value = int(
                32767
                * amplitude
                * max(0, ramp)
                * math.sin(2 * math.pi * hz * i / SAMPLE_RATE)
            )
            output.extend(struct.pack("<hh", value, value))
        output.extend(b"\0" * (int(0.08 * SAMPLE_RATE) * FRAME_BYTES))
    return bytes(output)


def speaker_test():
    """Play only a short quiet cue, without writing or modifying station files."""
    raw = make_cue()
    try:
        proc = subprocess.run(
            aplay_command(), input=raw, capture_output=True, timeout=8
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ALSA playback timed out") from exc
    if proc.returncode:
        raise RuntimeError(
            "ALSA playback failed: " + proc.stderr.decode(errors="replace")[-1000:]
        )
    return {
        "ok": True,
        "device": SINK,
        "format": "S16_LE stereo",
        "rate": SAMPLE_RATE,
        "duration_ms": len(raw) * 1000 // (SAMPLE_RATE * FRAME_BYTES),
        "quasar_modified": False,
        "chatgpt_connected": False,
    }
