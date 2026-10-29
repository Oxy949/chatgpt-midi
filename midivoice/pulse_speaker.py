"""PulseAudio pipe sink -> Station Midi ALSA default output (no firmware edits).

Pipe sink owns the FIFO. Native SoX supplies 96 kHz / four channels to the
existing ALSA 'default' route, avoiding host ALSA's rate conversion.

No PCM is stored, transmitted to APIs, or recorded. This is a playback bridge.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import select
import stat
import math
import struct
import sys
import subprocess

from .config import alpine_root
from .pulse import PULSE_SINK, speaker_fifo_path
from .speaker import aplay_command, CHANNELS, FRAME_BYTES, SAMPLE_RATE
from .processes import stop_signals, terminate

# 50 ms of 48kHz stereo signed 16 bit. Audio never crosses a shell.
CHUNK_SIZE = SAMPLE_RATE * FRAME_BYTES // 20
# The FIFO's latency is reported by PulseAudio; this extra anonymous pipe is
# invisible to it. 64 KiB here added up to 341 ms to playback/AEC timing.
PLAYBACK_PIPE_BYTES = 8192


def bound_playback_pipe(fd: int) -> int:
    """Bound the unreported playback queue without discarding any samples."""
    import fcntl

    fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, PLAYBACK_PIPE_BYTES)
    return fcntl.fcntl(fd, fcntl.F_GETPIPE_SZ)


def sox_command() -> list[str]:
    """Convert before host ALSA: avoid its Speex resampler after underruns."""
    return [
        sys.executable,
        "-m",
        "midivoice.alpine_exec",
        "sox",
        "-q",
        "-D",
        "--buffer",
        "2048",
        "-t",
        "raw",
        "-e",
        "signed-integer",
        "-b",
        "16",
        "-r",
        "48000",
        "-c",
        "2",
        "-L",
        "-",
        "-t",
        "raw",
        "-e",
        "signed-integer",
        "-b",
        "16",
        "-r",
        "96000",
        "-c",
        "4",
        "-L",
        "-",
        "rate",
        "-l",
        "96000",
        "remix",
        "1",
        "2",
        "1",
        "2",
    ]


def validate_sink_fifo(path: Path, root: Path | None = None) -> None:
    """Never read from arbitrary paths; protect session profile and audio."""
    if path != speaker_fifo_path(root):
        raise RuntimeError("Refusing unexpected speaker FIFO path")
    info = path.lstat()
    if not stat.S_ISFIFO(info.st_mode) or info.st_uid != 1000:
        raise RuntimeError("Speaker FIFO missing or ownership/type invalid")


def write_all(fd: int, data: bytes, stopping=None) -> None:
    """Write bounded PCM frames with partial-write handling."""
    mv = memoryview(data)
    while mv:
        if stopping is not None and stopping.is_set():
            return
        try:
            written = os.write(fd, mv)
        except BlockingIOError:
            select.select([], [fd], [], 0.25)
            continue
        if written <= 0:
            raise BrokenPipeError("aplay stopped receiving audio")
        mv = mv[written:]


def stream(root: Path | None = None) -> None:
    """Run PulseAudio speaker FIFO reader into ALSA, and stop on SIGTERM."""
    root = root if root is not None else alpine_root()
    path = speaker_fifo_path(root)
    validate_sink_fifo(path, root)
    if not (root / "usr/bin/sox").is_file():
        raise RuntimeError("Native SoX missing; run sh scripts/install-midi-audio.sh")
    with stop_signals() as stopping:
        _stream(root, path, stopping)


def _stream(root, path, stopping):
    fifo = None
    aplay = None
    native = None
    log = root.parent / "aplay.log"
    print(
        f"Midi Chromium sound: {PULSE_SINK} ({SAMPLE_RATE} Hz, {CHANNELS} ch) "
        "-> native SoX -> ALSA default (96000 Hz, 4 ch)",
        flush=True,
    )
    try:
        # Opening O_NONBLOCK prevents hanging indefinitely before PulseAudio
        # opens its end. Read waits via select, not a CPU-spinning loop.
        fifo = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with (root.parent / "speaker-sox.log").open("ab") as sox_log:
            native = subprocess.Popen(
                sox_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sox_log,
                bufsize=0,
                env={**os.environ, "MIDI_ALPINE_ROOT": str(root)},
            )
        if native.stdin is None or native.stdout is None:
            raise RuntimeError("SoX audio pipes not available")
        bound_playback_pipe(native.stdin.fileno())
        capacity = bound_playback_pipe(native.stdout.fileno())
        with log.open("ab") as stderr:
            aplay = subprocess.Popen(
                aplay_command(sample_rate=96000, channels=4),
                stdin=native.stdout,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                bufsize=0,
            )
        native.stdout.close()
        print(f"Native playback pipes bounded to {capacity} bytes each", flush=True)
        os.set_blocking(native.stdin.fileno(), False)
        pending = b""
        while not stopping.is_set():
            if native.poll() is not None:
                raise RuntimeError("Playback SoX exited; inspect speaker-sox.log")
            if aplay.poll() is not None:
                raise RuntimeError(
                    f"ALSA aplay exited ({aplay.returncode}); inspect {log}"
                )
            ready, _, _ = select.select([fifo], [], [], 0.25)
            if not ready:
                continue
            try:
                chunk = os.read(fifo, CHUNK_SIZE)
            except BlockingIOError:
                continue
            if not chunk:
                # PulseAudio can temporarily close its FIFO; don't spin.
                stopping.wait(0.05)
                continue
            # Some reads can cut an S16 stereo frame across boundaries.
            # Keep remainders so aplay always sees aligned PCM.
            pending += chunk
            aligned = len(pending) - len(pending) % FRAME_BYTES
            if aligned:
                write_all(native.stdin.fileno(), pending[:aligned], stopping)
                pending = pending[aligned:]
    finally:
        if fifo is not None:
            os.close(fifo)
        try:
            terminate(native)
        finally:
            terminate(aplay)
        print("Midi speaker bridge stopped", flush=True)


def cue_bytes() -> bytes:
    """Two short gentle notes for explicit opt-in PulseAudio->ALSA smoke test."""
    out = bytearray()
    for freq in (523.25, 659.25):
        count = int(0.15 * SAMPLE_RATE)
        fade = int(0.015 * SAMPLE_RATE)
        for i in range(count):
            envelope = min(1.0, i / fade, (count - 1 - i) / fade)
            value = int(
                32767
                * 0.10
                * max(0.0, envelope)
                * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
            )
            out.extend(struct.pack("<hh", value, value))
        out.extend(b"\0" * (int(0.08 * SAMPLE_RATE) * FRAME_BYTES))
    return bytes(out)


def test_tone(root: Path | None = None) -> None:
    """Explicit quiet cue through PulseAudio sink, not directly through ALSA."""
    root = root if root is not None else alpine_root()
    if not (root / "usr/bin/pacat").is_file():
        raise RuntimeError("pacat missing; run scripts/install-midi-audio.sh")
    cmd = [
        sys.executable,
        "-m",
        "midivoice.alpine_exec",
        "pacat",
        "--playback",
        "--device=" + PULSE_SINK,
        "--format=s16le",
        "--rate=48000",
        "--channels=2",
    ]
    result = subprocess.run(
        cmd,
        input=cue_bytes(),
        capture_output=True,
        timeout=12,
        env={**os.environ, "MIDI_ALPINE_ROOT": str(root)},
    )
    if result.returncode:
        raise RuntimeError(
            "PulseAudio playback failed: "
            + result.stderr.decode(errors="replace")[-700:]
        )
    print(
        "Test PCM delivered to PulseAudio midi_speaker. Audible sound requires ALSA aplay bridge."
    )


def main():
    parser = argparse.ArgumentParser(
        description="ChatGPT Chromium audio to Station Midi ALSA speaker"
    )
    parser.add_argument(
        "action", choices=("stream", "test"), nargs="?", default="stream"
    )
    args = parser.parse_args()
    if args.action == "stream":
        stream()
    else:
        test_tone()


if __name__ == "__main__":
    main()
