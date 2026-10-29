"""Private snapshot and extension diagnostics for the existing headless tab.

Do NOT upload raw HTML or screenshots publicly. They may include chat names,
account information, extension metadata, or other sensitive data.
"""

from __future__ import annotations

import json
import ipaddress
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_HTML_CHARS = 750_000
HTML_SNAPSHOT_JS = "(()=>document.documentElement ? document.documentElement.outerHTML.slice(0,750000) : '')()"


def page_structure_js() -> str:
    # Only DOM counts and coarse URL categories are returned in JSON.
    return r"""(()=>({
      origin: location.origin,
      page_type: location.pathname.startsWith('/c/') ? 'conversation' :
                 location.pathname === '/' ? 'home' : 'other',
      ready: document.readyState,
      buttons: document.querySelectorAll('button,[role="button"]').length,
      scripts: document.scripts.length,
      title_set: Boolean(document.title),
      main_present: Boolean(document.querySelector('main')),
      composer_present: Boolean(document.querySelector('textarea,[contenteditable="true"],[role="textbox"]')),
      body_text_chars: document.body?.innerText?.length ?? 0,
      html_chars: document.documentElement?.outerHTML?.length ?? 0,
      viewport: [innerWidth, innerHeight],
      visibility: document.visibilityState
    }))()"""


def write_private(path: Path, data: bytes):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)


def write_snapshot(
    folder: Path, structure: dict, html: str, image: bytes | None
) -> dict:
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    # One directory per snapshot; avoid replacing previous captures.
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = now + "-" + str(os.getpid())
    dest = folder / name
    for i in range(50):
        try:
            dest.mkdir(mode=0o700)
            break
        except FileExistsError:
            dest = folder / f"{name}-{i + 1}"
    else:
        raise RuntimeError("Cannot create snapshot directory")
    html_path = dest / "page.html"
    write_private(html_path, html.encode("utf-8", errors="replace"))
    write_private(
        dest / "structure.json",
        (json.dumps(structure, indent=2, ensure_ascii=False) + "\n").encode(),
    )
    png_path = None
    if image is not None:
        png_path = dest / "screen.png"
        write_private(png_path, image)
    return {
        "folder": str(dest),
        "html": str(html_path),
        "png": str(png_path) if png_path else None,
        "structure": structure,
        "html_truncated": len(html) >= MAX_HTML_CHARS,
        "warning": "Private files. HTML/PNG can contain personal chats or session data; review before sharing.",
    }


def extension_inventory(profile: Path) -> list[dict]:
    output = []
    for chrome_profile in (profile / "Default", *sorted(profile.glob("Profile *"))):
        if not chrome_profile.is_dir():
            continue
        prefs = {}
        pref_path = chrome_profile / "Preferences"
        try:
            # Upper bound to avoid loading an unexpectedly huge/corrupt file.
            if pref_path.stat().st_size < 8_000_000:
                prefs = json.loads(pref_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        settings = prefs.get("extensions", {}).get("settings", {})
        ext_dir = chrome_profile / "Extensions"
        if not ext_dir.is_dir():
            continue
        for ext in sorted(ext_dir.iterdir()):
            if not ext.is_dir() or not re.fullmatch(r"[a-p]{32}", ext.name):
                continue
            manifests = list(ext.glob("*/manifest.json"))
            if not manifests:
                continue
            try:
                manifest = json.loads(max(manifests).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
            ext_setting = settings.get(ext.name, {})
            # Chromium does not always persist a state field. Its absence is
            # inconclusive, especially for VPN extensions with sleeping workers.
            state = ext_setting.get("state")
            enabled = {0: False, 1: True}.get(state) if type(state) is int else None
            output.append(
                {
                    "profile": chrome_profile.name,
                    "id": ext.name,
                    "name": str(manifest.get("name", "?"))[:100],
                    "version": str(manifest.get("version", "?"))[:40],
                    "enabled_in_saved_preferences": enabled,
                }
            )
    return output


def active_extension_targets(infos: list[dict]) -> list[dict]:
    found = []
    for target in infos:
        url = target.get("url", "")
        match = re.match(r"chrome-extension://([a-p]{32})/", url)
        if match:
            found.append(
                {"id": match.group(1), "target_type": target.get("type", "unknown")}
            )
    return found[:30]


def decode_ip_response(body: str) -> str | None:
    try:
        address = json.loads(body).get("ip", "")
        return str(ipaddress.ip_address(address))
    except (ValueError, TypeError, AttributeError):
        return None


def voice_diagnostic(page: dict | None, issue: str) -> dict:
    """Only record structural page state. Never record chat text or auth data."""
    page = page or {}

    url = urlsplit(page.get("url") or "")
    origin = (
        "chatgpt.com"
        if url.scheme == "https" and url.netloc == "chatgpt.com"
        else "other"
    )
    return {
        "issue": issue,
        "site": origin,
        "page_type": (
            "conversation"
            if url.path.startswith("/c/")
            else "home"
            if url.path in ("/", "") and origin == "chatgpt.com"
            else "other"
        ),
        **{
            k: page.get(k)
            for k in (
                "ready",
                "buttons",
                "composer",
                "media_devices",
                "visibility",
                "voice_button",
                "end_button",
                "onboarding_button",
                "onboarding_stage",
                "verification_required",
                "voice_limit_reached",
                "login_required",
                "voice_candidates",
                "end_candidates",
                "hidden_voice_candidates",
                "viewport",
            )
        },
    }
