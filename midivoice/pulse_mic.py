"""Local, consent-only Station Midi microphone -> PulseAudio FIFO bridge.

No upload, recording, credential handling, or OpenAI API involvement. This sends
hardware PDM microphone audio to a named virtual PulseAudio source, which only
Chromium may access after the browser grants site microphone permission.
"""

from __future__ import annotations

import argparse
import errno
import os
from pathlib import Path
import select
import stat
import sys
import subprocess
import signal
import time
from contextlib import contextmanager

from .audio_format import (
    MIC_CHANNELS as CHANNELS,
    MIC_DEVICE,
    MIC_RATE as SAMPLE_RATE,
    VOICE_RATE as TARGET_RATE,
)
from .config import alpine_root
from .pulse import PULSE_SOURCE, MIC_CAPTURE_NAME, fifo_path, capture_environment
from . import pulse
from .processes import stop_signals, terminate


class TemporaryInput:
    """Root-only test signals suppress forwarding, never hardware capture.

    A synthetic PCM writer can use the existing FIFO without changing the
    browser's microphone device. Missing a resume cannot mute it indefinitely.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.until = 0.0

    def pause(self, *_):
        self.until = self.clock() + 10

    def resume(self, *_):
        self.until = 0.0

    def paused(self):
        return self.clock() < self.until


@contextmanager
def temporary_input_signals():
    control = TemporaryInput()
    previous = {}
    try:
        if hasattr(signal, "SIGUSR1"):
            for number, handler in (
                (signal.SIGUSR1, control.pause),
                (signal.SIGUSR2, control.resume),
            ):
                previous[number] = signal.signal(number, handler)
        yield control.paused
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def capture_command() -> list[str]:
    return [
        "arecord",
        "-q",
        "-D",
        MIC_CAPTURE_NAME,
        "-f",
        "S16_LE",
        "-c",
        str(CHANNELS),
        "-r",
        str(SAMPLE_RATE),
        "-t",
        "raw",
    ]


def write_audio_nonblocking(fd: int, pcm: bytes) -> bool:
    """Never block capture on a slow browser. Drop a frame instead."""
    if not pcm:
        return True
    try:
        n = os.write(fd, pcm)
        return n == len(pcm)
    except BlockingIOError:
        return False


def sox_command(channel: int = 1, gain_db: float = 24.0) -> list[str]:
    """Native resampling in the existing aarch64 Alpine chroot.

    No microphone hardware is exposed to the chroot: arecord on the host
    streams raw PCM to SoX over stdin. Keep resampling out of CPython.
    """
    if channel not in range(CHANNELS):
        raise ValueError("Channel must be between 0 and 3")
    if not -12 <= gain_db <= 36:
        raise ValueError("Gain must be between -12 and +36 dB")
    return [
        sys.executable,
        "-m",
        "midivoice.alpine_exec",
        "sox",
        "-q",
        "--buffer",
        "2048",
        "-t",
        "raw",
        "-e",
        "signed-integer",
        "-b",
        "16",
        "-r",
        str(SAMPLE_RATE),
        "-c",
        str(CHANNELS),
        "-L",
        "-",
        "-t",
        "raw",
        "-e",
        "signed-integer",
        "-b",
        "16",
        "-r",
        str(TARGET_RATE),
        "-c",
        "1",
        "-L",
        "-",
        "remix",
        str(channel + 1),
        "gain",
        str(gain_db),
        "rate",
        "-l",
        str(TARGET_RATE),
    ]


def stream(channel: int = 1, gain_db: float = 24, root: Path | None = None):
    """96 kHz 4-channel ALSA -> native SoX -> 16 kHz mono PulseAudio FIFO.

    Only lightweight I/O remains in Python, instead of ~1M FIR operations/s.
    FIFO is created by PulseAudio, not by this module. No audio saved.
    """
    root = root if root is not None else alpine_root()
    fifo = fifo_path(root)
    if not fifo.exists() or not stat.S_ISFIFO(fifo.lstat().st_mode):
        raise RuntimeError("Microphone FIFO missing; start PulseAudio first")
    if not (root / "usr/bin/sox").is_file():
        raise RuntimeError("Native SoX missing; run sh scripts/install-midi-audio.sh")
    args = sox_command(channel, gain_db)
    print(
        f"Midi virtual mic native SoX: {MIC_DEVICE} -> {PULSE_SOURCE}, "
        f"{TARGET_RATE}Hz mono, channel={channel}, gain={gain_db} dB",
        flush=True,
    )
    with stop_signals() as stopping, temporary_input_signals() as paused:
        _stream(root, fifo, args, stopping, paused)


def _stream(root, fifo, args, stopping, paused=None):
    mic = native = writer = None
    try:
        with (
            (root.parent / "arecord.log").open("ab") as capture_log,
            (root.parent / "sox.log").open("ab") as sox_log,
        ):
            mic = subprocess.Popen(
                capture_command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=capture_log,
                bufsize=0,
                env=capture_environment(root),
            )
            if mic.stdout is None:
                raise RuntimeError("arecord did not create an audio pipe")
            try:
                native = subprocess.Popen(
                    args,
                    stdin=mic.stdout,
                    stdout=subprocess.PIPE,
                    stderr=sox_log,
                    bufsize=0,
                    env={**os.environ, "MIDI_ALPINE_ROOT": str(root)},
                )
            finally:
                # Only SoX should hold the arecord pipe read end.
                mic.stdout.close()
        if native is None or native.stdout is None:
            raise RuntimeError("SoX did not create an audio pipe")
        pending = b""
        while not stopping.is_set():
            if mic.poll() is not None:
                raise RuntimeError("arecord exited; check arecord.log")
            if native.poll() is not None:
                raise RuntimeError("SoX exited; check sox.log")
            ready, _, _ = select.select([native.stdout], [], [], 0.25)
            if not ready:
                continue
            pcm = os.read(native.stdout.fileno(), 3200)
            if not pcm:
                raise RuntimeError("SoX audio pipe closed; check sox.log")
            pending += pcm
            aligned = len(pending) - len(pending) % 2
            pcm, pending = pending[:aligned], pending[aligned:]
            if paused is not None and paused():
                continue
            if writer is None:
                try:
                    writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                except OSError as exc:
                    if exc.errno not in (errno.ENXIO, errno.ENOENT):
                        raise
                    stopping.wait(0.1)
                    continue
            try:
                write_audio_nonblocking(writer, pcm)
            except BrokenPipeError:
                os.close(writer)
                writer = None
    finally:
        if writer is not None:
            os.close(writer)
        try:
            terminate(native)
        finally:
            terminate(mic)
        print("Midi native microphone stopped", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Station Midi ALSA to PulseAudio virtual microphone"
    )
    parser.add_argument("action", choices=("setup", "prepare", "secure", "stream"))
    parser.add_argument("--channel", type=int, default=1, choices=range(CHANNELS))
    parser.add_argument("--gain-db", type=float, default=24.0)
    args = parser.parse_args()
    if args.action == "setup":
        print(pulse.setup())
    elif args.action == "prepare":
        print(
            "Removed stale microphone FIFO"
            if pulse.clear_stale_fifo()
            else "No stale microphone FIFO"
        )
    elif args.action == "secure":
        pulse.secure_fifo()
        print("Microphone and speaker FIFOs secured (0600)")
    else:
        stream(args.channel, args.gain_db)


if __name__ == "__main__":
    main()
