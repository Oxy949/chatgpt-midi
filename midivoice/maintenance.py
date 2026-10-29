"""Manual login and host/browser probes; no daemon lifecycle."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .config import Settings
from .state import StateStore
from .processes import managed_process


def login(settings: Settings):
    """Manual, visible login in the real Chromium. No session scraping."""
    binary = shutil.which(settings.chromium)
    if not binary:
        raise SystemExit("Chromium is not installed")
    if not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
        raise SystemExit(
            "Login needs a visible desktop. Use an X11/Wayland session on the same host."
        )
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if Path(binary).name != "midi-chromium":
        settings.profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    print(
        "Log in to chatgpt.com manually and close the browser window. Keep profile private."
    )
    # Station Midi has no Chromium-compatible GPU/Vulkan driver. Keep the sandbox.
    from .browser_launch import low_resource_chromium_flags

    # Conservative resource settings for the 1 GB station, with an opt-out for
    # A/B diagnostics. Never turn off Chromium's sandbox.
    subprocess.run(
        [
            binary,
            f"--user-data-dir={settings.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-features=Vulkan",
            *low_resource_chromium_flags(visible=True, settings=settings),
            "https://chatgpt.com",
        ],
        check=True,
    )


def doctor(settings: Settings):
    import platform

    status = {
        "arch": platform.machine(),
        "python": platform.python_version(),
        "chromium": shutil.which(settings.chromium),
        "alsa_playback": shutil.which("aplay"),
        "alsa_capture": shutil.which("arecord"),
        "pulse_client": shutil.which("pactl"),
        "pulse_source": os.getenv("PULSE_SOURCE"),
        "pulse_sink": os.getenv("PULSE_SINK"),
        "profile_exists": settings.profile_dir.exists(),
        "resume_url_saved": StateStore(settings.state_dir).last_chat_url()
        != "https://chatgpt.com/",
        "mem_available_kb": None,
    }
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                status["mem_available_kb"] = int(line.split()[1])
    except OSError:
        pass
    print(json.dumps(status, ensure_ascii=False, indent=2))


def browser_probe(settings: Settings) -> int:
    """A real ARM browser-launch smoke test; no login or microphone involved."""
    import platform

    binary = shutil.which(settings.chromium)
    root = settings.alpine_root
    report = {
        "architecture": platform.machine(),
        "rootfs_present": (root / "sbin/apk").is_file(),
        "chromium_present": (root / "usr/bin/chromium").exists(),
        "launcher": binary,
        "sandbox_disabled": False,
        "passed": False,
    }
    if not binary:
        report["error"] = "Set MIDI_CHROMIUM to the Alpine launcher path"
        print(json.dumps(report, indent=2))
        return 1
    try:
        env = os.environ.copy()
        station = Path(binary).name == "midi-chromium"
        with tempfile.TemporaryDirectory(
            prefix="midi-probe-", dir=root / "tmp" if station else None
        ) as temporary:
            profile = Path(temporary)
            if station:
                os.chown(profile, 1000, 1000)
                env["MIDI_ALPINE_ROOT"] = str(root)
            command = [
                binary,
                f"--user-data-dir={profile}",
                "--headless=new",
                "--disable-gpu",
                "--disable-features=Vulkan",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-extensions",
                "--dump-dom",
                "about:blank",
            ]
            with managed_process(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
            ) as process:
                stdout, stderr = process.communicate(timeout=40)
                run = subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
        report["temporary_profile"] = True
        report["return_code"] = run.returncode
        report["passed"] = run.returncode == 0 and "<html" in run.stdout.lower()
        if not report["passed"]:
            report["stderr_tail"] = run.stderr[-1300:]
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        report["error"] = str(exc)[-500:]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1
