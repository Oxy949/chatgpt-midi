"""Run a browser or login helper in a private aarch64 Alpine root directory.

The host Python process must start as root to enter the chroot; it drops to
uid/gid 1000 *before* executing Chromium. Chromium's sandbox is NEVER disabled.
"""

from __future__ import annotations
import os
from pathlib import Path
import sys

from .config import alpine_root

ALLOWED = frozenset(
    {"chromium", "Xvfb", "x11vnc", "openbox", "pulseaudio", "pactl", "sox", "pacat"}
)


ALPINE_ENV = dict(
    HOME="/home/chatgpt",
    USER="chatgpt",
    LOGNAME="chatgpt",
    PATH="/usr/sbin:/usr/bin:/sbin:/bin",
    XDG_RUNTIME_DIR="/run/user/1000",
    XDG_CONFIG_HOME="/home/chatgpt/.config",
    XDG_CACHE_HOME="/home/chatgpt/.cache",
    PULSE_SERVER="unix:/run/user/1000/pulse/native",
    PULSE_SOURCE="midi_microphone",
    PULSE_SINK="midi_speaker",
)


def alpine_environment(parent=None):
    parent = os.environ if parent is None else parent
    env = dict(ALPINE_ENV)
    for key in ("DISPLAY", "LANG"):
        if parent.get(key):
            env[key] = parent[key]
    return env


def check_runtime(root: Path) -> None:
    if not (root / "proc/self/status").is_file():
        raise RuntimeError(
            "Alpine /proc is missing; run sh scripts/prepare-alpine-runtime.sh"
        )


def mapped_args(arguments: list[str], root: Path | None = None) -> list[str]:
    root = (root if root is not None else alpine_root()).resolve()
    result = []
    for a in arguments:
        if any(
            a == forbidden or a.startswith(forbidden + "=")
            for forbidden in (
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
                "--ignore-certificate-errors",
            )
        ):
            raise ValueError("Unsafe Chromium flag refused: " + a)
        if a.startswith("--user-data-dir="):
            value = Path(a.partition("=")[2])
            if not (value.is_absolute() or value.root):
                raise ValueError("Chromium profile must be absolute")
            value = value.resolve()
            try:
                relative = value.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    "Chromium profile must live inside Alpine rootfs"
                ) from exc
            a = "--user-data-dir=/" + relative.as_posix()
        result.append(a)
    return result


def main():
    root = alpine_root()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise SystemExit(
            "A root process is required only for entering the Alpine chroot"
        )
    if len(sys.argv) < 2 or Path(sys.argv[1]).name not in ALLOWED:
        raise SystemExit(
            "Usage: python -m midivoice.alpine_exec chromium|Xvfb|x11vnc|openbox|pulseaudio|pactl|sox|pacat [flags]"
        )
    binary = Path(sys.argv[1]).name
    paths = {
        "chromium": "/usr/bin/chromium",
        "Xvfb": "/usr/bin/Xvfb",
        "x11vnc": "/usr/bin/x11vnc",
        "openbox": "/usr/bin/openbox",
        "pulseaudio": "/usr/bin/pulseaudio",
        "pactl": "/usr/bin/pactl",
        "sox": "/usr/bin/sox",
        "pacat": "/usr/bin/pacat",
    }
    if not (root / "usr/bin" / binary).exists():
        raise SystemExit(f"Missing {binary} in {root}; run installer first")
    check_runtime(root)
    args = mapped_args(sys.argv[2:], root)
    env = alpine_environment()
    os.chroot(root)
    os.chdir("/home/chatgpt")
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    os.execve(paths[binary], [paths[binary], *args], env)


if __name__ == "__main__":
    main()
