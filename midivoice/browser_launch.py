"""Chromium launch policy, independent of CDP and the ChatGPT UI."""

from .config import Settings
from .errors import BrowserError


def low_resource_chromium_flags(
    visible: bool = False, settings: Settings | None = None
) -> list[str]:
    """Conservative memory reductions for the voice-only *headless* browser.

    Do not switch off site isolation, TLS checks, WebRTC, or Chromium sandbox.
    The visible login browser remains unchanged and can install extensions.
    MIDI_BROWSER_LOW_MEMORY=0 disables these additional reductions.
    """
    settings = settings or Settings.from_env()
    if not settings.low_memory:
        return []
    flags = [
        "--disable-dev-shm-usage",
        "--renderer-process-limit=1",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-print-preview",
        "--disable-notifications",
        "--disable-smooth-scrolling",
        "--disable-breakpad",
        "--disk-cache-size=33554432",  # 32 MiB on /data, not RAM
        "--force-device-scale-factor=1",
    ]
    # User can install extensions in the separate VNC login mode. They are
    # usually unnecessary for a voice-only shell and can consume lots of RAM.
    if not visible and not settings.headless_extensions:
        flags.append("--disable-extensions")
    return flags


def browser_display_mode(settings: Settings | None = None) -> str:
    """Use visible Chromium (like working VNC) when FASTVPN is essential."""
    settings = settings or Settings.from_env()
    mode = settings.browser_mode
    if mode not in ("headless", "visible"):
        raise ValueError("MIDI_BROWSER_MODE must be headless or visible")
    if mode == "visible" and not settings.display:
        raise BrowserError(
            "Visible Chromium requires DISPLAY from run-visible-voice.sh"
        )
    return mode


def voice_launch_flags(url: str, settings: Settings | None = None) -> list[str]:
    """Visible mode matches verified native login; preserves extensions/VPN.

    CDP automation itself could still trigger a security challenge.
    """
    settings = settings or Settings.from_env()
    mode = browser_display_mode(settings)
    flags = [
        f"--user-data-dir={settings.profile_dir}",
        "--disable-gpu",
        "--disable-features=Vulkan",
        "--remote-debugging-pipe",
        "--no-first-run",
        "--no-default-browser-check",
        "--autoplay-policy=no-user-gesture-required",
        "--force-prefers-reduced-motion",
        # Voice uses real-time JS/AudioWorklet scheduling even with a hidden UI.
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
    ]
    if mode == "visible":
        return [
            *flags,
            "--disable-dev-shm-usage",
            "--renderer-process-limit=1",
            "--disk-cache-size=33554432",
            url,
        ]
    return [
        *flags,
        "--headless=new",
        "--disable-sync",
        "--disable-translate",
        f"--window-size={voice_window_size(settings)}",
        f"--app={url}",
        *low_resource_chromium_flags(settings=settings),
    ]


def voice_window_size(settings: Settings | None = None) -> str:
    return (settings or Settings.from_env()).voice_window
