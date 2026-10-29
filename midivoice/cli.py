"""Command-line entry point. Application and transport logic live in modules."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import Settings
from .control import request_action, serve
from .maintenance import browser_probe, doctor, login
from . import __version__


async def request(action: str, settings: Settings, argument: str | None = None) -> int:
    async def progress(event):
        if event.get("stage") == "accepted":
            return
        page = event.get("page") or {}
        print(
            f"[{event.get('elapsed_s', 0)}s] {event.get('stage', 'loading')}: "
            f"site={page.get('site', '-')}, ready={page.get('ready', '-')}, "
            f"buttons={page.get('buttons', '-')}, voice={page.get('voice_button', '-')}",
            flush=True,
        )

    result = await request_action(
        action, settings.socket_path, progress=progress, argument=argument
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def build_parser():
    p = argparse.ArgumentParser(
        description="Small voice-only browser shell for ChatGPT"
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "action",
        choices=(
            "doctor",
            "login",
            "serve",
            "open",
            "new",
            "close",
            "status",
            "ui-show",
            "ui-hide",
            "audio-stats",
            "project",
            "mode-chat",
            "mode-work",
            "home",
            "diagnose",
            "snapshot",
            "vpn-check",
            "speech",
            "mic-check",
            "mic-sample",
            "speaker-test",
            "mic-serve",
            "quasar-listen",
            "speech-parse",
            "browser-probe",
        ),
    )
    p.add_argument(
        "phrase", nargs="?", help="Final speech recognized by Quasar (test adapter)"
    )
    p.add_argument(
        "--wake-verified", action="store_true", help="Quasar already verified wake word"
    )
    p.add_argument(
        "--dispatch",
        action="store_true",
        help="Forward Quasar commands to running local control service",
    )
    p.add_argument(
        "--cancel-stock",
        action="store_true",
        help="Cancel the matched Alice request as soon as the local controller accepts it",
    )
    p.add_argument(
        "--seconds",
        type=int,
        default=2,
        choices=range(1, 6),
        metavar="1..5",
        help="mic-check duration",
    )
    p.add_argument(
        "--channel",
        type=int,
        default=1,
        choices=range(4),
        help="Raw PDM microphone channel (0..3)",
    )
    p.add_argument(
        "--gain-db", type=float, default=24.0, help="Digital gain -12..+36 dB"
    )
    p.add_argument(
        "--output",
        default="/data/midi-voice-16k.wav",
        help="New WAV path, not overwritten",
    )
    p.add_argument(
        "--label",
        choices=("quiet", "speech", "sample"),
        default="sample",
        help="mic-check recording label",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cancel_stock and not args.dispatch:
        parser.error("--cancel-stock requires --dispatch")
    try:
        return dispatch(args, Settings.from_env())
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def dispatch(args, settings: Settings) -> int:
    if args.action == "quasar-listen":
        from .quasar_ipc import listen_quasar

        asyncio.run(
            listen_quasar(
                dispatch=args.dispatch,
                cancel_stock=args.cancel_stock,
                socket_path=str(settings.socket_path),
            )
        )
        return 0
    if args.action == "speech-parse":
        from .commands import parse_voice_command

        command = parse_voice_command(args.phrase, args.wake_verified)
        result = {"action": command.action if command else None}
        if command and command.argument is not None:
            result["argument"] = command.argument
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.action == "speaker-test":
        from .speaker import speaker_test

        print(json.dumps(speaker_test(), ensure_ascii=False, indent=2))
        return 0
    if args.action == "mic-serve":
        from .mic_server import serve_microphone

        asyncio.run(serve_microphone(args.channel, args.gain_db))
        return 0
    if args.action == "mic-sample":
        from .audio_stream import write_voice_sample

        print(
            json.dumps(
                write_voice_sample(
                    args.seconds, args.channel, args.gain_db, args.output
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.action == "mic-check":
        from .audio_probe import mic_check

        print(
            json.dumps(mic_check(args.seconds, args.label), ensure_ascii=True, indent=2)
        )
        return 0
    if args.action == "doctor":
        doctor(settings)
        return 0
    if args.action == "browser-probe":
        return browser_probe(settings)
    if args.action == "login":
        login(settings)
        return 0
    if args.action == "serve":
        asyncio.run(serve(settings=settings))
        return 0
    try:
        if args.action == "speech":
            from .commands import parse_voice_command

            command = parse_voice_command(args.phrase, args.wake_verified)
            if not command:
                print(
                    json.dumps(
                        {"ok": True, "action": None, "reason": "ignored"},
                        ensure_ascii=False,
                    )
                )
                return 0
            return asyncio.run(request(command.action, settings, command.argument))
        return asyncio.run(
            request(
                args.action, settings, args.phrase if args.action == "project" else None
            )
        )
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        print(
            f"Daemon not running. Start: python3 -m midivoice serve ({exc})",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
