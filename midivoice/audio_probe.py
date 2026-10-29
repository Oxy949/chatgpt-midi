"""Read-only, bounded ALSA microphone probe for Station Midi (Cucumber).

This is a *diagnostic* only. It does not stream audio to ChatGPT or change
Quasar, mixer settings, ALSA configuration, or device sample rates.
"""

from __future__ import annotations

from array import array
import json
import math
import shutil
import subprocess
import sys

from .audio_format import (
    MIC_DEVICE,
    MIC_CHANNELS as CHANNELS,
    MIC_RATE as SAMPLE_RATE,
    SAMPLE_BYTES,
)

FULL_SCALE = 32768.0


def analyze_pcm(raw: bytes, channels: int = CHANNELS) -> dict:
    """Analyze interleaved little-endian 16-bit PCM without saving audio."""
    if channels <= 0 or len(raw) % (channels * SAMPLE_BYTES):
        raise ValueError("Expected complete interleaved S16_LE frames")
    if sys.byteorder != "little":
        raise RuntimeError("This probe expects little-endian CPU samples")
    samples = array("h")
    samples.frombytes(raw)
    frames = len(samples) // channels
    if not frames:
        raise ValueError("No samples returned by ALSA")
    result = []
    for i in range(channels):
        chan = samples[i::channels]
        squares = sum(int(x) * int(x) for x in chan)
        rms = math.sqrt(squares / len(chan))
        peak = max(abs(int(v)) for v in chan)
        clipping = sum(1 for v in chan if abs(int(v)) >= 32767)
        result.append(
            {
                "index": i,
                "rms": round(rms, 2),
                "rms_dbfs": round(20 * math.log10(max(rms, 1e-12) / FULL_SCALE), 2)
                if rms
                else None,
                "peak": peak,
                "peak_dbfs": round(20 * math.log10(peak / FULL_SCALE), 2)
                if peak
                else None,
                "clipped_samples": clipping,
            }
        )
    return {"bytes": len(raw), "frames": frames, "channels": result}


def mic_check(seconds: int = 2, label: str = "sample") -> dict:
    if not 1 <= seconds <= 5:
        raise ValueError("seconds must be between 1 and 5")
    if not shutil.which("arecord"):
        raise RuntimeError("arecord is not installed")
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
        proc = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds + 7
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ALSA capture timed out; stop and check Quasar") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            "ALSA capture failed: " + proc.stderr.decode(errors="replace")[-1000:]
        )
    data = analyze_pcm(proc.stdout)
    data.update(
        {
            "device": MIC_DEVICE,
            "format": "S16_LE",
            "sample_rate": SAMPLE_RATE,
            "label": label,
            "audio_saved": False,
        }
    )
    return data


def main():
    import argparse

    p = argparse.ArgumentParser(
        description="Station Midi bounded ALSA microphone check"
    )
    p.add_argument(
        "--seconds", type=int, default=2, choices=range(1, 6), metavar="1..5"
    )
    p.add_argument("--label", default="sample", choices=["quiet", "speech", "sample"])
    args = p.parse_args()
    print(
        f"Capture {args.label}: {args.seconds}s, {MIC_DEVICE}, 4ch/96000/S16_LE",
        flush=True,
    )
    print(json.dumps(mic_check(args.seconds, args.label), indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
