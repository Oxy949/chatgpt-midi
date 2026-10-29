"""Temporary ownership of the Midi RGB logo; ring and clock stay with Alice."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .state import write_private_json

LOGO = tuple(
    f"cucumber:{color}:logo{pixel}"
    for pixel in range(6)
    for color in ("red", "green", "blue")
)
SYNC = "cucumber:logo:sync"
COLORS = {
    "starting": (96, 40, 0),
    "voice": (0, 96, 64),
    "verification": (64, 0, 80),
    "chat": (0, 48, 96),
    "work": (72, 24, 96),
}


class NoIndicator:
    error = None

    def show(self, mode: str) -> None:
        pass


class LogoIndicator:
    """Save kernel LED triggers before use and restore them on close/restart.

    Writes occur only at lifecycle transitions, never in the audio loop.
    The private journal also permits recovery after a killed controller.
    """

    def __init__(self, state_dir: Path, leds: Path = Path("/sys/class/leds")):
        self.leds = leds
        self.journal = state_dir / "logo-original.json"
        self.saved: dict | None = None
        self.mode = None
        self.error: str | None = None

    def _write(self, name, attribute, value):
        (self.leds / name / attribute).write_text(str(value) + "\n", encoding="ascii")

    def _load(self):
        if not self.journal.exists():
            return None
        if self.journal.stat().st_size > 8192:
            raise ValueError("Invalid logo recovery journal")
        data = json.loads(self.journal.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {*LOGO, SYNC}:
            raise ValueError("Invalid logo recovery channels")
        for value in data.values():
            if (
                not isinstance(value, dict)
                or type(value.get("brightness")) is not int
                or not 0 <= value["brightness"] <= 255
                or not re.fullmatch(
                    r"[a-zA-Z0-9_-]{1,64}", str(value.get("trigger", ""))
                )
            ):
                raise ValueError("Invalid logo recovery values")
        return data

    def _restore(self):
        saved = self.saved or self._load()
        if saved is None:
            return
        # Write original values while detached, then reconnect firmware triggers.
        for name in LOGO:
            self._write(name, "brightness", saved[name]["brightness"])
        self._write(SYNC, "brightness", 255)
        self._write(SYNC, "brightness", saved[SYNC]["brightness"])
        for name in (*LOGO, SYNC):
            self._write(name, "trigger", saved[name]["trigger"])
        self.journal.unlink(missing_ok=True)
        self.saved = None

    def _acquire(self):
        self._restore()  # Recover an interrupted previous session first.
        saved = {}
        for name in (*LOGO, SYNC):
            folder = self.leds / name
            trigger = re.search(r"\[([^\]]+)\]", (folder / "trigger").read_text())
            if trigger is None:
                raise ValueError("Cannot identify active logo trigger")
            maximum = int((folder / "max_brightness").read_text())
            brightness = int((folder / "brightness").read_text())
            if maximum != 255 or not 0 <= brightness <= maximum:
                raise ValueError("Unsupported logo brightness range")
            saved[name] = {"trigger": trigger[1], "brightness": brightness}
        write_private_json(self.journal, saved)
        self.saved = saved
        for name in (*LOGO, SYNC):
            self._write(name, "trigger", "none")

    def show(self, mode: str) -> None:
        if mode == self.mode and not self.error:
            return
        try:
            if mode not in COLORS:
                self._restore()
            else:
                if self.saved is None:
                    self._acquire()
                for name, value in zip(LOGO, COLORS[mode] * 6):
                    self._write(name, "brightness", value)
                self._write(SYNC, "brightness", 255)
            self.mode = mode
            self.error = None
        except (OSError, ValueError, TypeError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            # Indicator failure must never stop a conversation.
            try:
                self._restore()
            except (OSError, ValueError, TypeError):
                pass


def create_indicator(settings):
    if settings.indicator == "off":
        return NoIndicator()
    if not Path("/sys/class/leds", SYNC, "trigger").is_file():
        return NoIndicator()
    indicator = LogoIndicator(settings.state_dir)
    # Recover stale ownership even if the previous process died before a close.
    indicator.show("idle")
    return indicator
