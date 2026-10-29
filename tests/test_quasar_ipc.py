import struct
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from midivoice.quasar_ipc import (
    RecognizedCommand,
    CommandConsumer,
    bytes_field,
    cancel_native_reply,
    fields,
    integer_field,
    recognize_frame,
    wire_int,
    wire_bytes,
    pause_native_player,
)

REQ = "123e4567-e89b-12d3-a456-426614174000"


def make_event(
    phrase: str, *, state=3, final=True, finished=True, event_type=3, request_id=REQ
):
    speech = (
        wire_bytes(1, phrase.encode("utf-8"))
        + wire_int(2, 1 if final else 0)
        + wire_int(4, 1 if finished else 0)
    )
    event = (
        wire_int(1, state)
        + wire_bytes(7, request_id.encode("ascii"))
        + wire_bytes(15, speech)
    )
    return wire_bytes(252, event) + wire_int(253, event_type)


class QuasarFrameTests(unittest.TestCase):
    def test_named_project_is_a_separate_argument(self):
        command = recognize_frame(make_event("Алиса, открой проект Мой дом в ChatGPT"))
        self.assertEqual(command, RecognizedCommand("project", REQ, "мой дом"))

    def test_player_pause_packet_is_separate_from_dialog_cancel(self):
        packet = pause_native_player()
        self.assertEqual(struct.unpack("<I", packet[:4])[0], len(packet) - 4)
        outer = fields(packet[4:])
        remote = fields(bytes_field(outer, 252))
        self.assertEqual(bytes_field(remote, 7), b"PlaybackControlCapability")
        self.assertEqual(integer_field(fields(bytes_field(remote, 13)), 1), 1)

    def test_match_exact_open(self):
        record = recognize_frame(make_event("открой чат джипити"))
        self.assertEqual(record, RecognizedCommand("open", REQ))

    def test_match_exact_close(self):
        record = recognize_frame(make_event("Алиса, отключи ChatGPT"))
        self.assertEqual(record.action, "close")

    def test_reject_nonfinal_and_nonbusy(self):
        cases = [
            make_event("запусти ChatGPT", state=1),
            make_event("запусти ChatGPT", final=False),
            make_event("запусти ChatGPT", finished=False),
            make_event("запусти ChatGPT", event_type=210),
            make_event("запусти ChatGPT", request_id="invalid"),
            make_event("сделай новый чат"),
            b"\xff\xff\xff",
            make_event("запусти ChatGPT") + b"\x00",
        ]
        for frame in cases:
            with self.subTest(frame=frame[:12]):
                self.assertIsNone(recognize_frame(frame))

    def test_cancel_scoped_to_request(self):
        packet = cancel_native_reply(REQ)
        self.assertEqual(struct.unpack("<I", packet[:4])[0], len(packet) - 4)
        outer = fields(packet[4:])
        self.assertEqual(integer_field(outer, 253), 210)
        remote = fields(bytes_field(outer, 252))
        self.assertEqual(bytes_field(remote, 7), b"AliceCapability")
        method = fields(bytes_field(remote, 8))
        self.assertEqual(integer_field(method, 1), 6)
        self.assertEqual(bytes_field(method, 10), REQ.encode())


class MockWriter:
    def __init__(self):
        self.values = []

    def write(self, data):
        self.values.append(data)

    async def drain(self):
        return None


class QuasarDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancels_on_acceptance_before_slow_startup_finishes(self):
        client = CommandConsumer(
            dispatch=True, cancel_stock=True, socket_path="/fake.sock"
        )
        writer = MockWriter()
        accepted = asyncio.Event()
        finish = asyncio.Event()

        async def dispatch(action, path, *, progress):
            await progress({"stage": "accepted"})
            accepted.set()
            await finish.wait()
            return {"ok": False}  # Even a later Voice quota must not start Alice music.

        with patch("midivoice.quasar_ipc.send_action", side_effect=dispatch):
            task = asyncio.create_task(
                client.handle(make_event("открой ChatGPT"), writer)
            )
            await asyncio.wait_for(accepted.wait(), 1)
            self.assertFalse(task.done())
            self.assertEqual(writer.values, [cancel_native_reply(REQ)])
            await client.observe_state(
                wire_bytes(252, wire_int(1, 0)) + wire_int(253, 3), writer
            )
            self.assertEqual(writer.values[-1], pause_native_player())
            finish.set()
            await task
        self.assertEqual(len(writer.values), 2)

    async def test_a_new_wake_cancels_the_pending_music_pause(self):
        client = CommandConsumer(dispatch=True, cancel_stock=True)
        client.pause_until = float("inf")
        writer = MockWriter()
        for state in (1, 0):
            await client.observe_state(
                wire_bytes(252, wire_int(1, state)) + wire_int(253, 3), writer
            )
        self.assertEqual(writer.values, [])

    async def test_listener_accepts_close_while_open_is_pending(self):
        from midivoice.quasar_ipc import listen_quasar

        reader = asyncio.StreamReader()
        for phrase, identifier in (
            ("открой ChatGPT", REQ),
            ("закрой ChatGPT", "123e4567-e89b-12d3-a456-426614174001"),
        ):
            frame = make_event(phrase, request_id=identifier)
            reader.feed_data(struct.pack("<I", len(frame)) + frame)
        writer = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
        closed = asyncio.Event()

        async def dispatch(action, path):
            if action == "open":
                await asyncio.Event().wait()
            closed.set()
            return {"ok": True}

        with (
            patch(
                "midivoice.quasar_ipc.asyncio.open_connection",
                new=AsyncMock(return_value=(reader, writer)),
            ),
            patch("midivoice.quasar_ipc.send_action", side_effect=dispatch),
        ):
            task = asyncio.create_task(
                listen_quasar(
                    dispatch=True, cancel_stock=False, socket_path="/fake.sock"
                )
            )
            try:
                await asyncio.wait_for(closed.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        writer.close.assert_called_once()

    async def test_preview_only_and_dedup(self):
        client = CommandConsumer()
        writer = MockWriter()
        frame = make_event("Алиса, запусти ChatGPT")
        with patch("midivoice.quasar_ipc.send_action", new_callable=AsyncMock) as send:
            self.assertEqual((await client.handle(frame, writer)).action, "open")
            self.assertIsNone(await client.handle(frame, writer))
            send.assert_not_called()
        self.assertEqual(writer.values, [])

    async def test_successful_dispatch_no_cancel_unless_explicit(self):
        client = CommandConsumer(dispatch=True, socket_path="/tmp/test.sock")
        writer = MockWriter()
        with patch(
            "midivoice.quasar_ipc.send_action",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as send:
            await client.handle(make_event("закрой ChatGPT"), writer)
            send.assert_awaited_once_with("close", "/tmp/test.sock")
        self.assertEqual(writer.values, [])

    async def test_cancel_stock_only_after_success(self):
        client = CommandConsumer(
            dispatch=True, cancel_stock=True, socket_path="/tmp/test.sock"
        )
        writer = MockWriter()
        with patch(
            "midivoice.quasar_ipc.send_action",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ):
            await client.handle(make_event("запусти ChatGPT"), writer)
        self.assertEqual(writer.values, [])
        client2 = CommandConsumer(
            dispatch=True, cancel_stock=True, socket_path="/tmp/test.sock"
        )
        with patch(
            "midivoice.quasar_ipc.send_action",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ):
            await client2.handle(make_event("запусти ChatGPT"), writer)
        self.assertEqual(len(writer.values), 1)


if __name__ == "__main__":
    unittest.main()
