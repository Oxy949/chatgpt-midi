"""Shared local PulseAudio layout and FIFO/config lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import time

from .config import alpine_root
from .audio_format import VOICE_RATE as TARGET_RATE


FIFO_DIR = "midi-chatgpt-audio"
FIFO_NAME = "midi-microphone.pcm"
SPEAKER_FIFO_NAME = "midi-speaker.pcm"
PULSE_CONFIG = "midi-pulse.pa"
PULSE_SOURCE = "midi_microphone"
PULSE_SINK = "midi_speaker"
MIC_CAPTURE_NAME = "midi_capture"
ALSA_CAPTURE_CONFIG = "midi-mic-alsa.conf"


def isolated_alsa_config_path(root: Path | None = None) -> Path:
    """Host-only ALSA override; avoids unrelated broken /etc/alsa/conf.d files."""
    root = root if root is not None else alpine_root()
    return root.parent / ALSA_CAPTURE_CONFIG


def isolated_alsa_config_text() -> str:
    """Minimal ALSA hw PCM with no system-wide config hooks or includes."""
    return "pcm.midi_capture {\n    type hw\n    card 0\n    device 1\n}\n"


def prepare_isolated_alsa(root: Path | None = None) -> Path:
    root = root if root is not None else alpine_root()
    path = isolated_alsa_config_path(root)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Refusing unsafe ALSA config path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(isolated_alsa_config_text(), encoding="ascii")
    path.chmod(0o600)
    return path


def capture_environment(root: Path | None = None) -> dict[str, str]:
    """Override ALSA config for only the dedicated mic subprocess.

    Keep aplay and Yandex's audio paths on their existing configurations.
    """
    root = root if root is not None else alpine_root()
    env = os.environ.copy()
    env["ALSA_CONFIG_PATH"] = str(prepare_isolated_alsa(root))
    return env


def fifo_path(root: Path | None = None) -> Path:
    root = root if root is not None else alpine_root()
    return root / "tmp" / FIFO_DIR / FIFO_NAME


def speaker_fifo_path(root: Path | None = None) -> Path:
    root = root if root is not None else alpine_root()
    return root / "tmp" / FIFO_DIR / SPEAKER_FIFO_NAME


def pulse_config() -> str:
    """Use local-only PulseAudio, no ALSA card probing and no network protocol."""
    return "\n".join(
        (
            "# Local-only virtual Midi microphone; never expose a TCP listener.",
            "load-module module-native-protocol-unix",
            f"load-module module-pipe-source source_name={PULSE_SOURCE} file=/tmp/{FIFO_DIR}/{FIFO_NAME} format=s16le rate={TARGET_RATE} channels=1",
            f"load-module module-pipe-sink sink_name={PULSE_SINK} file=/tmp/{FIFO_DIR}/{SPEAKER_FIFO_NAME} format=s16le rate=48000 channels=2",
            f"set-default-source {PULSE_SOURCE}",
            f"set-default-sink {PULSE_SINK}",
            "",
        )
    )


def setup(root: Path | None = None) -> dict:
    """Prepare private /tmp directory and PulseAudio config, NOT the FIFO.

    PulseAudio's module-pipe-source must create the FIFO itself. Creating it
    beforehand breaks older PulseAudio builds with EEXIST (as seen on Midi).
    """
    root = root if root is not None else alpine_root()
    if not (root / "sbin" / "apk").is_file():
        raise RuntimeError(f"Alpine not present: {root}")
    temp = root / "tmp"
    if not temp.is_dir() or temp.is_symlink():
        raise RuntimeError(f"Invalid Alpine /tmp: {temp}")
    directory = temp / FIFO_DIR
    if directory.is_symlink():
        raise RuntimeError("Refusing symlink at microphone directory")
    if directory.exists() and not directory.is_dir():
        raise RuntimeError("Microphone directory path is not a directory")
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, 1000, 1000)
    os.chmod(directory, 0o700)
    for fifo in (fifo_path(root), speaker_fifo_path(root)):
        try:
            mode = fifo.lstat().st_mode
        except FileNotFoundError:
            pass  # PulseAudio creates each FIFO itself.
        else:
            if not stat.S_ISFIFO(mode):
                raise RuntimeError(f"Audio FIFO path exists but is not a FIFO: {fifo}")
    home = root / "home" / "chatgpt"
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / PULSE_CONFIG
    if cfg.is_symlink():
        raise RuntimeError("Refusing symlink in PulseAudio config path")
    cfg.write_text(pulse_config(), encoding="utf-8")
    cfg.chmod(0o600)
    os.chown(cfg, 1000, 1000)
    prepare_isolated_alsa(root)
    return {
        "mic_fifo": str(fifo_path(root)),
        "speaker_fifo": str(speaker_fifo_path(root)),
        "pulse_config": str(cfg),
        "source": PULSE_SOURCE,
        "sink": PULSE_SINK,
    }


def _audio_fifos(root: Path | None = None) -> tuple[Path, Path]:
    root = root if root is not None else alpine_root()
    return fifo_path(root), speaker_fifo_path(root)


def clear_stale_fifo(root: Path | None = None) -> bool:
    """Remove only previously created FIFOs owned by the dedicated Alpine user.

    Caller must stop the old PulseAudio session before invoking.
    """
    root = root if root is not None else alpine_root()
    found = []
    for fifo in _audio_fifos(root):
        try:
            info = fifo.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISFIFO(info.st_mode):
            raise RuntimeError(f"Refusing non-FIFO audio path: {fifo}")
        if info.st_uid != 1000:
            raise RuntimeError(f"Refusing FIFO not owned by Alpine user: {fifo}")
        found.append(fifo)
    for fifo in found:
        fifo.unlink()
    return bool(found)


def secure_fifo(root: Path | None = None, timeout_seconds: float = 3.0) -> None:
    """Restrict PulseAudio-created FIFOs to chatgpt, never silently accept a ghost server.

    Audio source/sink presence via `pactl` alone is not sufficient: a stale
    PulseAudio instance can still list devices after its FIFO was unlinked.
    """
    root = root if root is not None else alpine_root()
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    for fifo in _audio_fifos(root):
        while True:
            try:
                info = fifo.lstat()
            except FileNotFoundError:
                if time.monotonic() < deadline:
                    time.sleep(0.1)
                    continue
                raise RuntimeError(
                    f"Missing PulseAudio FIFO: {fifo}. This can happen when an old "
                    "PulseAudio server is still running. Stop the previous VNC/headless "
                    "session before restarting. For VPN setup use MIDI_LOGIN_NO_AUDIO=1."
                ) from None
            if not stat.S_ISFIFO(info.st_mode) or info.st_uid != 1000:
                raise RuntimeError(f"PulseAudio FIFO has unexpected owner/type: {fifo}")
            os.chmod(fifo, 0o600)
            break
