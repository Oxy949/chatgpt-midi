"""Allow only explicit ChatGPT open/close commands from final, wake-verified ASR.

No generic 'chat' commands, no accidental matches inside longer sentences.
"""

import re
from dataclasses import dataclass

_OPEN_VERBS = frozenset({"запусти", "открой"})
_CLOSE_VERBS = frozenset({"закрой", "отключи"})
# The same four commands, with common spellings of the product name as ASR sees it.
_NAMES = frozenset(
    {
        "chatgpt",
        "chat gpt",
        "chat g p t",
        "чат gpt",
        "чат g p t",
        "чат джипити",
        "чат джи пи ти",
        "чат гпт",
        "чат гэпэтэ",
        "чат г п т",
    }
)


@dataclass(frozen=True)
class VoiceCommand:
    action: str
    argument: str | None = None


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", value.casefold().replace("ё", "е")).split())


def validate_argument(action: str, argument: str | None) -> None:
    if action == "project":
        if (
            not isinstance(argument, str)
            or not 1 <= len(argument.strip()) <= 128
            or any(ord(c) < 32 for c in argument)
        ):
            raise ValueError("Project name must be 1..128 characters")
    elif argument is not None:
        raise ValueError("This action takes no argument")


def parse_voice_command(transcript: str, wake_verified: bool) -> VoiceCommand | None:
    """Require verified wake, a product name, and an entire supported command."""
    if not wake_verified or not isinstance(transcript, str):
        return None
    phrase = normalize_name(transcript)
    if phrase.startswith("алиса "):
        phrase = phrase[len("алиса ") :]
    verb, sep, name = phrase.partition(" ")
    if sep and name in _NAMES:
        if verb in _OPEN_VERBS:
            return VoiceCommand("open")
        if verb in _CLOSE_VERBS:
            return VoiceCommand("close")
    for product in sorted(_NAMES, key=len, reverse=True):
        command = None
        switch = "переключи " + product + " в режим "
        if phrase.startswith(switch):
            command = "режим " + phrase[len(switch) :]
        elif phrase.startswith(product + " "):
            command = phrase[len(product) + 1 :]
        elif phrase.endswith(" в " + product):
            command = phrase[: -(len(product) + 3)]
        elif phrase.endswith(" " + product):
            command = phrase[: -(len(product) + 1)]
        if command is None:
            continue
        if command in {
            "новый чат",
            "новая вкладка",
            "открой новый чат",
            "создай новый чат",
            "открой новую вкладку",
        }:
            return VoiceCommand("new")
        if command in {"главная", "обычный чат", "выйди из проекта", "закрой проект"}:
            return VoiceCommand("home")
        if command in {
            "чат",
            "режим чат",
            "режим чата",
            "включи режим чат",
            "включи режим чата",
            "переключи в режим чат",
            "переключи в режим чата",
        }:
            return VoiceCommand("mode-chat")
        if command in {
            "работа",
            "режим работа",
            "режим работы",
            "включи режим работы",
            "переключи в режим работы",
            "переключи в режим работа",
        }:
            return VoiceCommand("mode-work")
        for prefix in ("открой проект ", "проект "):
            if command.startswith(prefix):
                argument = command[len(prefix) :]
                if 1 <= len(argument) <= 128 and not re.search(
                    r"\bи (открой|включи|запусти)\b", argument
                ):
                    return VoiceCommand("project", argument)
    return None


def parse_final_speech(transcript: str, wake_verified: bool) -> str | None:
    """Compatibility helper for consumers which need only an action label."""
    command = parse_voice_command(transcript, wake_verified)
    return command.action if command else None
