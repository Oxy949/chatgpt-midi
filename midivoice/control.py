"""Shared JSON-lines control protocol for the CLI, daemon and Quasar adapter."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import stat
import signal
from typing import Awaitable, Callable

from .config import Settings
from .controller import Controller
from .indicators import create_indicator
from .commands import validate_argument

ACTIONS = (
    "open",
    "new",
    "close",
    "status",
    "snapshot",
    "vpn-check",
    "diagnose",
    "ui-show",
    "ui-hide",
    "audio-stats",
    "project",
    "mode-chat",
    "mode-work",
    "home",
)
MAX_REQUEST = 4096
MAX_REPLY = 64 * 1024
Progress = Callable[[dict], Awaitable[None]]


def encode_message(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


async def read_message(
    reader: asyncio.StreamReader, *, timeout: float, limit: int
) -> dict:
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout)
    except ValueError as exc:
        raise ValueError("Control message is too long") from exc
    if not raw:
        raise ConnectionError("Voice controller disconnected")
    if len(raw) > limit or not raw.endswith(b"\n"):
        raise ValueError("Invalid or oversized control message")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Control message must be an object")
    return data


async def close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(ConnectionError, OSError):
        await writer.wait_closed()


async def request_action(
    action: str,
    socket_path: str | Path,
    *,
    progress: Progress | None = None,
    timeout: float = 950,
    argument: str | None = None,
) -> dict:
    if not hasattr(asyncio, "open_unix_connection"):
        raise RuntimeError("Local controller sockets require Linux/POSIX")
    if action not in ACTIONS:
        raise ValueError("Unknown control action")
    validate_argument(action, argument)
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(socket_path), limit=MAX_REPLY), 2
    )
    try:
        message = {"action": action}
        if argument is not None:
            message["argument"] = argument
        writer.write(encode_message(message))
        await asyncio.wait_for(writer.drain(), 2)
        # One overall deadline: progress must not extend the request indefinitely.
        async with asyncio.timeout(timeout):
            while True:
                reply = await read_message(reader, timeout=timeout, limit=MAX_REPLY)
                if reply.get("event") == "progress":
                    if progress is not None:
                        await progress(reply)
                    continue
                if not isinstance(reply.get("ok"), bool):
                    raise ValueError("Control response has no boolean ok field")
                return reply
    finally:
        await close_writer(writer)


async def handle_client(reader, writer, controller: Controller) -> None:
    async def report(event):
        # Disconnected terminals must not interrupt a valid voice startup.
        with contextlib.suppress(ConnectionError, OSError):
            writer.write(encode_message(event))
            await writer.drain()

    try:
        try:
            message = await read_message(reader, timeout=8, limit=MAX_REQUEST)
            action = message.get("action")
            if action not in ACTIONS:
                raise ValueError("Unknown control action")
            argument = message.get("argument")
            validate_argument(action, argument)
            if argument is None:
                result = await controller.handle(action, progress=report)
            else:
                result = await controller.handle(
                    action, argument=argument, progress=report
                )
        except Exception as exc:
            result = {"ok": False, "error": str(exc) or type(exc).__name__}
        with contextlib.suppress(ConnectionError, OSError):
            writer.write(encode_message(result))
            await writer.drain()
    finally:
        await close_writer(writer)


async def prepare_socket(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode):
        raise RuntimeError(f"Refusing non-socket control path: {path}")
    try:
        _, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 2)
    except (ConnectionRefusedError, FileNotFoundError):
        path.unlink(missing_ok=True)
    else:
        await close_writer(writer)
        raise RuntimeError("A daemon is already running")


async def serve(
    *, settings: Settings | None = None, controller: Controller | None = None
):
    if not hasattr(asyncio, "start_unix_server"):
        raise RuntimeError("The voice daemon requires Linux/POSIX")
    settings = settings or Settings.from_env()
    with controller_lease(settings.socket_path):
        await _serve(settings, controller)


@contextlib.contextmanager
def controller_lease(path: Path):
    """Hold an advisory lock before probing or replacing the control socket."""
    import fcntl

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(
        path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A daemon is already running") from None
        yield
    finally:
        os.close(fd)


async def _serve(settings: Settings, controller: Controller | None):
    path = settings.socket_path
    await prepare_socket(path)
    controller = controller or Controller(
        settings=settings, indicator=create_indicator(settings)
    )
    clients: set[asyncio.Task] = set()

    async def client(reader, writer):
        task = asyncio.current_task()
        clients.add(task)
        try:
            await handle_client(reader, writer, controller)
        finally:
            clients.discard(task)

    server = await asyncio.start_unix_server(client, path=str(path), limit=MAX_REQUEST)
    path.chmod(0o600)
    print(f"Voice shell listening on {path}", flush=True)
    loop = asyncio.get_running_loop()
    previous_term = signal.getsignal(signal.SIGTERM)
    serving = asyncio.current_task()

    def shutdown():
        if not serving.cancelling():
            serving.cancel()

    loop.add_signal_handler(signal.SIGTERM, shutdown)
    watcher = (
        asyncio.create_task(controller.watch())
        if isinstance(controller, Controller)
        else None
    )
    try:
        async with server:
            await server.serve_forever()
    finally:
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        loop.remove_signal_handler(signal.SIGTERM)
        signal.signal(signal.SIGTERM, previous_term)
        server.close()
        await server.wait_closed()
        pending = list(clients)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        try:
            await controller.handle("close")
        finally:
            path.unlink(missing_ok=True)
