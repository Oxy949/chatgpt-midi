"""On-demand private local PCM socket for Station Midi microphones.

Wire protocol: fixed 320-byte frames, 10ms each, mono S16_LE at 16 kHz.
No microphone data is written to files, transmitted over TCP or sent to OpenAI.
A single authenticated-by-file-permission local client is allowed.
The stream is a raw sensor path, not an echo-cancelled voice path.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket

from .audio_format import MIC_CHANNELS as CHANNELS, MIC_DEVICE, MIC_RATE as SAMPLE_RATE
from .audio_stream import Mono16k, FRAME_BYTES, TARGET_RATE
from .config import Settings
from .control import prepare_socket, close_writer

INPUT_10MS = (SAMPLE_RATE // 100) * FRAME_BYTES  # 7680 bytes
OUTPUT_10MS = (TARGET_RATE // 100) * 2  # 320 bytes
MAX_QUEUED = 6  # 60ms in userland; disconnect a stalled client.


class MicrophoneCapture:
    def __init__(self, channel: int = 1, gain_db: float = 24.0):
        self.converter = Mono16k(channel=channel, gain_db=gain_db)
        self.proc = None
        self.queue = asyncio.Queue(maxsize=MAX_QUEUED)
        self.worker = None
        self.dropped_frames = 0
        self.error = None

    async def start(self):
        binary = shutil.which("arecord")
        if not binary:
            raise RuntimeError("arecord not installed")
        # 96 kHz is REQUIRED: changing PDM rate can disrupt native Alice.
        self.proc = await asyncio.create_subprocess_exec(
            binary,
            "-q",
            "-D",
            MIC_DEVICE,
            "-f",
            "S16_LE",
            "-c",
            str(CHANNELS),
            "-r",
            str(SAMPLE_RATE),
            "-t",
            "raw",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.worker = asyncio.create_task(self._producer())

    async def _producer(self):
        try:
            while True:
                raw = await asyncio.wait_for(
                    self.proc.stdout.readexactly(INPUT_10MS), timeout=3
                )
                data = self.converter.process(raw)
                if len(data) != OUTPUT_10MS:
                    raise RuntimeError("Bad resampler output frame size")
                if self.queue.full():
                    self.queue.get_nowait()
                    self.dropped_frames += 1
                self.queue.put_nowait(data)
        except asyncio.CancelledError:
            raise
        except (
            asyncio.IncompleteReadError,
            asyncio.TimeoutError,
            OSError,
            RuntimeError,
        ) as exc:
            self.error = str(exc)
            # Wake the consumer even on a failure without a queued audio frame.
            if self.queue.full():
                self.queue.get_nowait()
            self.queue.put_nowait(None)

    async def next_frame(self):
        frame = await self.queue.get()
        if frame is None:
            raise RuntimeError("Capture stopped: " + str(self.error))
        return frame

    async def close(self):
        if self.worker:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        self.proc = self.worker = None


async def serve_microphone(channel: int = 1, gain_db: float = 24.0):
    # Validate arguments before creating the socket.
    Mono16k(channel=channel, gain_db=gain_db)
    directory = Settings.from_env().state_dir
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "microphone.pcm.sock"
    await prepare_socket(path)

    lock = asyncio.Lock()
    clients = set()

    async def client(reader, writer):
        task = asyncio.current_task()
        if lock.locked():
            await close_writer(writer)
            return
        async with lock:
            clients.add(task)
            capture = MicrophoneCapture(channel, gain_db)
            try:
                await capture.start()
                # Disallow accumulating userland PCM if the receiving client stalls.
                writer.transport.set_write_buffer_limits(
                    high=OUTPUT_10MS * MAX_QUEUED, low=OUTPUT_10MS * 2
                )
                # Limit kernel socket buffering to reduce latent backlogs.
                sock = writer.get_extra_info("socket")
                if sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                while True:
                    frame = await capture.next_frame()
                    if writer.is_closing():
                        return
                    writer.write(frame)
                    await asyncio.wait_for(writer.drain(), timeout=0.10)
            except (RuntimeError, OSError, ConnectionError, asyncio.TimeoutError):
                pass
            finally:
                try:
                    await capture.close()
                    await close_writer(writer)
                finally:
                    clients.discard(task)

    server = await asyncio.start_unix_server(client, path=str(path))
    path.chmod(0o600)
    print(
        f"Raw mic PCM stream: {path} (16000/S16_LE/mono, 10ms, single client)",
        flush=True,
    )
    print(
        "The microphones start only when a local client connects. Ctrl+C to stop.",
        flush=True,
    )
    try:
        async with server:
            await server.serve_forever()
    finally:
        pending = list(clients)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        path.unlink(missing_ok=True)
