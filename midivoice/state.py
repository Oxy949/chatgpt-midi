"""Private local state and validated conversation links."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import re
from urllib.parse import urlsplit
from uuid import UUID

from .config import CHATGPT_HOME


def safe_chat_url(value: str | None) -> str | None:
    try:
        url = urlsplit(value or "")
        if url.scheme != "https" or url.netloc != "chatgpt.com":
            return None
        parts = url.path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[0] == "g"
            and parts[2] == "c"
            and re.fullmatch(r"g-p-[a-zA-Z0-9-]{16,80}", parts[1])
        ):
            return f"https://chatgpt.com/g/{parts[1]}/c/{UUID(parts[3])}"
        if len(parts) != 2 or parts[0] != "c":
            return None
        return f"https://chatgpt.com/c/{UUID(parts[1])}"
    except (ValueError, AttributeError, TypeError):
        return None


def write_private_json(path: Path, data: dict) -> None:
    """Replace atomically with a private file, including on concurrent writes."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class StateStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.conversation = directory / "conversation.json"
        self.diagnostic = directory / "voice-diagnostic.json"

    def last_chat_url(self) -> str:
        try:
            data = json.loads(self.conversation.read_text(encoding="utf-8"))
            return safe_chat_url(data["url"]) or CHATGPT_HOME
        except (OSError, ValueError, KeyError, TypeError):
            return CHATGPT_HOME

    def save_chat_url(self, url: str) -> bool:
        clean = safe_chat_url(url)
        if clean is None:
            return False
        write_private_json(self.conversation, {"url": clean})
        return True

    def write_diagnostic(self, data: dict) -> None:
        write_private_json(self.diagnostic, data)

    def read_diagnostic(self) -> dict | None:
        try:
            data = json.loads(self.diagnostic.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None
