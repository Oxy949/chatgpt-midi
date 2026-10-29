"""Local, optional Quasar aliced speech-event adapter for rooted Midi.

Consumes only final cloud ASR state events on 127.0.0.1:9875.
No firmware patches, LD_PRELOAD, microphone interception or remote listener.
Frame structure is based on the experimental native_agent.c in
rison1337/Alisa-ChatGPT-voice-assistant, and MUST be validated on Midi.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import struct
import time
from uuid import UUID

from .commands import parse_voice_command
from .control import request_action

MAX_FRAME = 512 * 1024


class QuasarProtocolError(ValueError):
    pass


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    out = 0
    for shift in range(0, 70, 7):
        if pos >= len(buf):
            raise QuasarProtocolError("Truncated varint")
        octet = buf[pos]
        pos += 1
        if shift == 63 and octet > 1:
            raise QuasarProtocolError("Varint overflow")
        out |= (octet & 0x7F) << shift
        if not octet & 0x80:
            return out, pos
    raise QuasarProtocolError("Varint too long")


def fields(buf: bytes) -> dict[int, tuple[int, bytes | int]]:
    """Decode the first instance of protobuf fields with standard wire types."""
    pos = 0
    result = {}
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        tag, wire = key >> 3, key & 7
        if tag == 0:
            raise QuasarProtocolError("Zero field number")
        if wire == 0:
            value, pos = read_varint(buf, pos)
        elif wire == 1:
            if pos + 8 > len(buf):
                raise QuasarProtocolError("Truncated fixed64")
            value, pos = buf[pos : pos + 8], pos + 8
        elif wire == 2:
            length, pos = read_varint(buf, pos)
            if length > len(buf) - pos:
                raise QuasarProtocolError("Truncated bytes")
            value, pos = buf[pos : pos + length], pos + length
        elif wire == 5:
            if pos + 4 > len(buf):
                raise QuasarProtocolError("Truncated fixed32")
            value, pos = buf[pos : pos + 4], pos + 4
        else:
            raise QuasarProtocolError(f"Unsupported wire type {wire}")
        result.setdefault(tag, (wire, value))
    return result


def bytes_field(items: dict, tag: int) -> bytes | None:
    wire, value = items.get(tag, (None, None))
    return value if wire == 2 and isinstance(value, bytes) else None


def integer_field(items: dict, tag: int) -> int:
    wire, value = items.get(tag, (None, 0))
    return value if wire == 0 and isinstance(value, int) else 0


@dataclass(frozen=True)
class RecognizedCommand:
    action: str
    request_id: str
    argument: str | None = None


def recognize_frame(frame: bytes) -> RecognizedCommand | None:
    """Quasar state 3 (BUSY), final recognition only, verified request-id.

    No transcript is stored or logged. Successful Quasar recognition is treated
    as wake-verified for this particular event channel.
    """
    try:
        if len(frame) > MAX_FRAME:
            return None
        outer = fields(frame)
        if integer_field(outer, 253) != 3:
            return None
        payload = bytes_field(outer, 252)
        if payload is None:
            return None
        event = fields(payload)
        if integer_field(event, 1) != 3:  # BUSY, not LISTENING
            return None
        request_id = bytes_field(event, 7)
        speech = bytes_field(event, 15)
        if not request_id or not speech:
            return None
        identifier = request_id.decode("ascii")
        if len(identifier) != 36 or str(UUID(identifier)) != identifier:
            return None
        recognized = fields(speech)
        if integer_field(recognized, 2) != 1 or integer_field(recognized, 4) != 1:
            return None
        phrase = bytes_field(recognized, 1)
        if not phrase or len(phrase) > 1024:
            return None
        command = parse_voice_command(phrase.decode("utf-8"), wake_verified=True)
        return (
            RecognizedCommand(command.action, identifier, command.argument)
            if command
            else None
        )
    except (QuasarProtocolError, UnicodeDecodeError, ValueError, OverflowError):
        return None


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Negative varint")
    encoded = bytearray()
    while value >= 128:
        encoded.append((value & 127) | 128)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def wire_bytes(tag: int, payload: bytes) -> bytes:
    return encode_varint(tag * 8 + 2) + encode_varint(len(payload)) + payload


def wire_int(tag: int, number: int) -> bytes:
    return encode_varint(tag * 8) + encode_varint(number)


def cancel_native_reply(request_id: str) -> bytes:
    """Experimental AliceCapability method 6 scoped to a completed request.

    Only send with an explicit opt-in; Midi firmware compatibility is unverified.
    """
    if str(UUID(request_id)) != request_id or len(request_id) != 36:
        raise ValueError("Invalid request ID")
    method = wire_int(1, 6) + wire_bytes(10, request_id.encode("ascii"))
    remote = wire_bytes(7, b"AliceCapability") + wire_bytes(8, method)
    envelope = wire_bytes(252, remote) + wire_int(253, 210)
    return struct.pack("<I", len(envelope)) + envelope


def pause_native_player() -> bytes:
    """One pause request via the firmware's PlaybackControlCapability."""
    source = wire_int(1, 14) + wire_int(2, 3) + wire_int(3, 1)
    source += wire_bytes(4, b"00000000-0000-4000-8000-000000000001")
    source += wire_bytes(6, b"ChatGPTVoice")
    method = wire_int(1, 1) + wire_bytes(5, source) + wire_int(7, 0)
    remote = wire_bytes(7, b"PlaybackControlCapability") + wire_bytes(13, method)
    envelope = wire_bytes(252, remote) + wire_int(253, 210)
    return struct.pack("<I", len(envelope)) + envelope


def native_state(frame: bytes) -> int | None:
    try:
        outer = fields(frame)
        if integer_field(outer, 253) != 3:
            return None
        payload = bytes_field(outer, 252)
        if payload is None:
            return None
        state = integer_field(fields(payload), 1)
        return state if state in (0, 1, 2, 3, 4, 5) else None
    except QuasarProtocolError:
        return None


async def send_action(action: str, socket_path: str, **kwargs) -> dict:
    return await request_action(action, socket_path, **kwargs)


class CommandConsumer:
    """Deduplicate Quasar final events and dispatch only allowed actions."""

    def __init__(
        self,
        *,
        dispatch: bool = False,
        cancel_stock: bool = False,
        socket_path: str = "",
    ):
        if cancel_stock and not dispatch:
            raise ValueError("Cancelling Alice requires dispatch mode")
        self.dispatch = dispatch
        self.cancel_stock = cancel_stock
        self.socket_path = socket_path
        self.ids: deque[str] = deque(maxlen=128)
        self.native_state = None
        self.pause_until = 0.0
        self.wake_revision = 0

    async def observe_state(self, frame, writer):
        state = native_state(frame)
        if state is None:
            return
        self.native_state = state
        if state == 1:  # A later wake belongs to the user's next request.
            self.pause_until = 0.0
            self.wake_revision += 1
        if state == 0 and self.pause_until > time.monotonic():
            self.pause_until = 0.0
            writer.write(pause_native_player())
            await asyncio.wait_for(writer.drain(), 2)

    async def handle(
        self, data: bytes, writer: asyncio.StreamWriter
    ) -> RecognizedCommand | None:
        return await self.handle_command(recognize_frame(data), writer)

    async def handle_command(
        self, command: RecognizedCommand | None, writer: asyncio.StreamWriter
    ) -> RecognizedCommand | None:
        if command is None or command.request_id in self.ids:
            return None
        self.ids.append(command.request_id)
        if not self.dispatch:
            print(
                f"Quasar command detected: {command.action} (preview only)", flush=True
            )
            return command
        cancelled = False
        wake_revision = self.wake_revision

        async def accepted(event):
            nonlocal cancelled
            if not self.cancel_stock or cancelled or event.get("stage") != "accepted":
                return
            cancelled = True
            self.pause_until = (
                time.monotonic() + 5
                if command.action != "close" and self.wake_revision == wake_revision
                else 0.0
            )
            writer.write(cancel_native_reply(command.request_id))
            await asyncio.wait_for(writer.drain(), 2)
            if self.native_state == 0 and self.pause_until > time.monotonic():
                self.pause_until = 0.0
                writer.write(pause_native_player())
                await asyncio.wait_for(writer.drain(), 2)
            print("Native Alice request cancelled at controller acceptance", flush=True)

        try:
            options = {}
            if command.argument is not None:
                options["argument"] = command.argument
            if self.cancel_stock:
                options["progress"] = accepted
            reply = await send_action(command.action, self.socket_path, **options)
        except (OSError, asyncio.TimeoutError, RuntimeError, ValueError) as error:
            print(
                f"Quasar command {command.action}: control unavailable ({type(error).__name__})",
                flush=True,
            )
            return command
        if not reply.get("ok"):
            print(
                f"Quasar command {command.action}: rejected by local controller",
                flush=True,
            )
            return command
        print(f"Quasar command dispatched: {command.action}", flush=True)
        if self.cancel_stock and not cancelled:
            try:
                writer.write(cancel_native_reply(command.request_id))
                await asyncio.wait_for(writer.drain(), 2)
            except (OSError, asyncio.TimeoutError, ValueError):
                print("Unable to suppress the native Alice reply", flush=True)
        return command


async def listen_quasar(
    *, dispatch: bool, cancel_stock: bool, socket_path: str, port: int = 9875
):
    """Local IPC client; reconnect on firmware restart, no shell hooks."""
    consumer = CommandConsumer(
        dispatch=dispatch, cancel_stock=cancel_stock, socket_path=socket_path
    )
    while True:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), 3
            )
            print("Connected to local Quasar speech events", flush=True)
            pending: set[asyncio.Task] = set()
            try:
                while True:
                    header = await reader.readexactly(4)
                    length = struct.unpack("<I", header)[0]
                    if length < 1 or length > MAX_FRAME:
                        raise QuasarProtocolError("Invalid IPC frame length")
                    data = await reader.readexactly(length)
                    await consumer.observe_state(data, writer)
                    command = recognize_frame(data)
                    if command is None or command.request_id in consumer.ids:
                        continue
                    # Keep reading so a spoken close can cancel a slow open.
                    # Only command labels/IDs remain in these bounded tasks.
                    if len(pending) >= 8 and (
                        command.action != "close" or len(pending) >= 9
                    ):
                        print("Quasar controller busy; command ignored", flush=True)
                        continue
                    task = asyncio.create_task(consumer.handle_command(command, writer))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                    await asyncio.sleep(0)
            finally:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                writer.close()
                await writer.wait_closed()
        except (
            OSError,
            asyncio.IncompleteReadError,
            asyncio.TimeoutError,
            QuasarProtocolError,
        ) as error:
            print(
                f"Quasar IPC disconnected ({type(error).__name__}); retrying",
                flush=True,
            )
            await asyncio.sleep(2)
