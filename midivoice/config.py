"""Runtime settings. Read the environment at the application boundary, not import."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
from typing import Mapping

DEFAULT_ALPINE_ROOT = Path("/data/opt/midi-browser/alpine")
CHATGPT_HOME = "https://chatgpt.com/"


def alpine_root() -> Path:
    return Path(os.environ.get("MIDI_ALPINE_ROOT", str(DEFAULT_ALPINE_ROOT)))


def voice_wait_seconds(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        value = int(env.get("MIDI_VOICE_WAIT_SECONDS", "360"))
    except ValueError:
        raise ValueError(
            "MIDI_VOICE_WAIT_SECONDS must be an integer (60..900)"
        ) from None
    if not 60 <= value <= 900:
        raise ValueError("MIDI_VOICE_WAIT_SECONDS must be 60..900")
    return value


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    profile_dir: Path
    alpine_root: Path = DEFAULT_ALPINE_ROOT
    chromium: str = "chromium"
    browser_mode: str = "headless"
    display: str = ""
    voice_wait: int = 360
    voice_window: str = "800,600"
    low_memory: bool = True
    headless_extensions: bool = False
    extra_flags: tuple[str, ...] = ()
    indicator: str = "auto"
    quiet_ui: bool = True

    @property
    def socket_path(self) -> Path:
        return self.state_dir / "control.sock"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        state = Path(
            env.get(
                "MIDI_STATE_DIR", str(Path.home() / ".local/state/midi-chatgpt-voice")
            )
        )
        mode = env.get("MIDI_BROWSER_MODE", "headless")
        if mode not in ("headless", "visible"):
            raise ValueError("MIDI_BROWSER_MODE must be headless or visible")
        indicator = env.get("MIDI_INDICATOR", "auto")
        if indicator not in ("auto", "off"):
            raise ValueError("MIDI_INDICATOR must be auto or off")
        window = env.get("MIDI_VOICE_WINDOW", "800,600")
        if not re.fullmatch(r"([3-9]\d\d|1[0-2]\d\d),([3-9]\d\d|1[0-2]\d\d)", window):
            raise ValueError("MIDI_VOICE_WINDOW must be WIDTH,HEIGHT (300..1299)")
        return cls(
            state_dir=state,
            profile_dir=Path(
                env.get("MIDI_BROWSER_PROFILE_DIR", str(state / "browser-profile"))
            ),
            alpine_root=Path(env.get("MIDI_ALPINE_ROOT", str(DEFAULT_ALPINE_ROOT))),
            chromium=env.get("MIDI_CHROMIUM", "chromium"),
            browser_mode=mode,
            display=env.get("DISPLAY", ""),
            voice_wait=voice_wait_seconds(env),
            voice_window=window,
            low_memory=env.get("MIDI_BROWSER_LOW_MEMORY", "1") != "0",
            headless_extensions=env.get("MIDI_HEADLESS_EXTENSIONS", "0") == "1",
            extra_flags=tuple(shlex.split(env.get("MIDI_CHROMIUM_EXTRA_FLAGS", ""))),
            indicator=indicator,
            quiet_ui=env.get("MIDI_BROWSER_QUIET_UI", "1") != "0",
        )
