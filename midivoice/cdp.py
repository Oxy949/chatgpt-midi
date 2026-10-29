"""Multiplexed CDP over private POSIX pipes, with explicit resource ownership."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import subprocess
import signal

from .cdp_spawn import build_cdp_launch
from .errors import BrowserError

MAX_MESSAGE = 8 * 1024 * 1024


class CDPConnection:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.writer: int | None = None
        self.transport: asyncio.ReadTransport | None = None
        self.read_task: asyncio.Task | None = None
        self.requests: dict[int, asyncio.Future] = {}
        self.write_waiters: set[asyncio.Future] = set()
        self.number = 0
        self.write_lock = asyncio.Lock()
        self.error: BrowserError | None = None
        self.log_path: Path | None = None

    async def start(
        self,
        binary: str,
        flags: list[str],
        profile: Path,
        log_path: Path,
        *,
        alpine_root: Path | None = None,
        display: str | None = None,
    ) -> list[str]:
        if os.name != "posix":
            raise BrowserError(
                "Chromium CDP pipes require Linux/POSIX; run on the station"
            )
        if self.proc is not None:
            await self.stop(force=True)
        self.error = None
        self.log_path = log_path
        owned: set[int] = set()
        pipe = None
        try:
            browser_read, parent_write = os.pipe()
            owned.update((browser_read, parent_write))
            parent_read, browser_write = os.pipe()
            owned.update((parent_read, browser_write))
            cmd, env, effective = build_cdp_launch(
                binary,
                flags,
                browser_read,
                browser_write,
                profile,
                root=alpine_root,
                display=display,
            )
            log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "ab") as log:
                log.write(b"\n==== Midi Chromium launch (staged diagnostics) ====\n")
                log.flush()
                self.proc = subprocess.Popen(
                    cmd,
                    env=env,
                    pass_fds=(browser_read, browser_write),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            # Parent must close the child's endpoints before waiting for EOF.
            for fd in (browser_read, browser_write):
                owned.remove(fd)
                os.close(fd)
            self.writer = parent_write
            owned.remove(parent_write)
            os.set_blocking(parent_write, False)
            reader = asyncio.StreamReader(limit=MAX_MESSAGE)
            pipe = os.fdopen(parent_read, "rb", buffering=0)
            owned.remove(parent_read)
            self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), pipe
            )
            pipe = None  # The read transport now owns it.
            self.read_task = asyncio.create_task(self._receive(reader))
            return effective
        except BaseException:
            await self.stop(force=True)
            raise
        finally:
            if pipe is not None:
                pipe.close()
            for fd in owned:
                os.close(fd)

    def _fail_pending(self, error: BrowserError) -> None:
        self.error = error
        for future in self.requests.values():
            if not future.done():
                future.set_exception(error)
        self.requests.clear()
        for waiter in self.write_waiters:
            if not waiter.done():
                waiter.set_exception(error)

    async def _receive(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                raw = await reader.readuntil(b"\0")
                data = json.loads(raw[:-1])
                if not isinstance(data, dict):
                    raise ValueError("CDP message must be an object")
                if "error" in data and not isinstance(data["error"], dict):
                    raise ValueError("Invalid CDP error response")
                if data.get("method") == "Inspector.targetCrashed":
                    self._fail_pending(
                        BrowserError("Chromium renderer crashed; inspect chromium.log")
                    )
                    return
                future = self.requests.pop(data.get("id"), None)
                if future is None or future.done():
                    continue  # Unsolicited CDP event or a timed-out response.
                if "error" in data:
                    future.set_exception(
                        BrowserError(str(data["error"].get("message", "CDP error")))
                    )
                else:
                    future.set_result(data.get("result", {}))
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
        ):
            await asyncio.sleep(0.05)
            code = self.proc.poll() if self.proc else None
            detail = (
                f"Chromium exited with code {code}"
                if code is not None
                else "Chromium closed its debugging pipe"
            )
            self._fail_pending(BrowserError(f"{detail}; inspect {self.log_path}"))

    async def _write(self, payload: bytes) -> None:
        loop = asyncio.get_running_loop()
        async with self.write_lock:
            view = memoryview(payload)
            while view:
                if self.error:
                    raise self.error
                if self.writer is None:
                    raise BrowserError("Browser is not started")
                fd = self.writer
                try:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise BrokenPipeError("CDP pipe stopped receiving data")
                    view = view[count:]
                except BlockingIOError:
                    ready = loop.create_future()
                    self.write_waiters.add(ready)

                    def writable():
                        if not ready.done():
                            ready.set_result(None)

                    loop.add_writer(fd, writable)
                    try:
                        await ready
                    finally:
                        self.write_waiters.discard(ready)
                        loop.remove_writer(fd)
                except OSError as exc:
                    raise BrowserError(
                        "Unable to write to Chromium debugging pipe"
                    ) from exc

    async def send(
        self,
        method: str,
        params: dict | None = None,
        *,
        session: str | None = None,
        timeout: float = 12,
    ):
        if self.error:
            raise self.error
        if self.writer is None:
            raise BrowserError("Browser is not started")
        self.number += 1
        index = self.number
        request = {"id": index, "method": method, "params": params or {}}
        if session is not None:
            request["sessionId"] = session
        payload = json.dumps(request).encode() + b"\0"
        if len(payload) > MAX_MESSAGE:
            raise BrowserError("CDP request is too large")
        future = asyncio.get_running_loop().create_future()
        self.requests[index] = future
        try:
            async with asyncio.timeout(timeout):
                await self._write(payload)
                return await future
        finally:
            self.requests.pop(index, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Retrieve an error even if writing failed first.

    async def stop(self, force: bool = False) -> None:
        proc = self.proc
        try:
            if proc and proc.poll() is None:
                if not force:
                    with contextlib.suppress(Exception):
                        await self.send("Browser.close", timeout=2)
                stages = (("terminate", 2), ("kill", 2))
                if not force:
                    stages = ((None, 3), *stages)
                for signal_name, timeout in stages:
                    if signal_name is not None and proc.poll() is None:
                        with contextlib.suppress(ProcessLookupError):
                            getattr(proc, signal_name)()
                    try:
                        await asyncio.to_thread(proc.wait, timeout=timeout)
                        break
                    except subprocess.TimeoutExpired:
                        continue
        finally:
            if proc and os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            self._fail_pending(BrowserError("Browser stopped"))
            if self.read_task:
                self.read_task.cancel()
                await asyncio.gather(self.read_task, return_exceptions=True)
                self.read_task = None
            if self.transport:
                self.transport.close()
                self.transport = None
            if self.writer is not None:
                os.close(self.writer)
                self.writer = None
            self.proc = None
