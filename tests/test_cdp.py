import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from midivoice.cdp import CDPConnection
from midivoice.errors import BrowserError


class CDPTests(unittest.IsolatedAsyncioTestCase):
    async def test_renderer_crash_fails_pending_without_waiting_for_timeout(self):
        connection = CDPConnection()
        connection.writer = 42
        reader = asyncio.StreamReader()
        with patch("midivoice.cdp.os.write", side_effect=lambda fd, data: len(data)):
            request = asyncio.create_task(connection.send("Runtime.evaluate"))
            await asyncio.sleep(0)
            reader.feed_data(
                b'{"method":"Inspector.targetCrashed","sessionId":"page"}\0'
            )
            await asyncio.wait_for(connection._receive(reader), 0.2)
            with self.assertRaisesRegex(BrowserError, "renderer crashed"):
                await request
            with self.assertRaisesRegex(BrowserError, "renderer crashed"):
                await connection.send("AfterCrash")

    async def test_multiplexes_out_of_order_replies_and_ignores_events(self):
        connection = CDPConnection()
        connection.writer = 42
        frames = []
        reader = asyncio.StreamReader()
        receive = asyncio.create_task(connection._receive(reader))

        def write(fd, payload):
            frames.append(json.loads(bytes(payload).rstrip(b"\0")))
            return len(payload)

        with patch("midivoice.cdp.os.write", write):
            first = asyncio.create_task(connection.send("First"))
            second = asyncio.create_task(connection.send("Second", session="page"))
            await asyncio.sleep(0)
            reader.feed_data(json.dumps({"method": "Page.loaded"}).encode() + b"\0")
            for frame in reversed(frames):
                reader.feed_data(
                    json.dumps(
                        {"id": frame["id"], "result": {"method": frame["method"]}}
                    ).encode()
                    + b"\0"
                )
            self.assertEqual(await first, {"method": "First"})
            self.assertEqual(await second, {"method": "Second"})
        self.assertEqual(frames[1]["sessionId"], "page")
        self.assertEqual(connection.requests, {})
        receive.cancel()
        await asyncio.gather(receive, return_exceptions=True)

    async def test_partial_writes_preserve_message(self):
        connection = CDPConnection()
        connection.writer = 42
        output = bytearray()

        def partial(fd, data):
            count = min(3, len(data))
            output.extend(data[:count])
            return count

        with patch("midivoice.cdp.os.write", partial):
            await connection._write(b"abcdefghijk")
        self.assertEqual(output, b"abcdefghijk")

    async def test_backpressure_yields_until_writable(self):
        connection = CDPConnection()
        connection.writer = 42
        loop = asyncio.get_running_loop()
        with (
            patch("midivoice.cdp.os.write", side_effect=[BlockingIOError(), 3]),
            patch.object(
                loop,
                "add_writer",
                side_effect=lambda fd, callback: loop.call_soon(callback),
            ),
            patch.object(loop, "remove_writer") as remove,
        ):
            await connection._write(b"abc")
        remove.assert_called_once_with(42)

    async def test_eof_or_malformed_reply_fails_pending_and_future_requests(self):
        for response in (b"", b"[]\0", b'{"id":1,"error":[]}\0', b"not-json\0"):
            connection = CDPConnection()
            connection.writer = 42
            reader = asyncio.StreamReader()
            with patch(
                "midivoice.cdp.os.write", side_effect=lambda fd, data: len(data)
            ):
                request = asyncio.create_task(connection.send("Pending"))
                await asyncio.sleep(0)
                reader.feed_data(response)
                reader.feed_eof()
                await connection._receive(reader)
                with self.assertRaises(BrowserError):
                    await request
                with self.assertRaises(BrowserError):
                    await connection.send("AfterEOF")
            self.assertEqual(connection.requests, {})

    async def test_timeout_removes_request(self):
        connection = CDPConnection()
        connection.writer = 42
        with patch("midivoice.cdp.os.write", side_effect=lambda fd, data: len(data)):
            with self.assertRaises(TimeoutError):
                await connection.send("NoReply", timeout=0.01)
        self.assertEqual(connection.requests, {})

    async def test_launch_failure_closes_every_pipe(self):
        connection = CDPConnection()
        descriptors = []
        real_pipe = os.pipe

        def pipe():
            pair = real_pipe()
            descriptors.extend(pair)
            return pair

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with (
                patch("midivoice.cdp.os.name", "posix"),
                patch("midivoice.cdp.os.pipe", pipe),
                patch(
                    "midivoice.cdp.build_cdp_launch",
                    side_effect=RuntimeError("invalid profile"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "invalid profile"):
                    await connection.start("fake", [], folder, folder / "log")
        self.assertEqual(len(descriptors), 4)
        for fd in descriptors:
            with self.assertRaises(OSError):
                os.fstat(fd)

    async def test_spawn_failure_closes_every_pipe(self):
        connection = CDPConnection()
        descriptors = []
        real_pipe = os.pipe

        def pipe():
            pair = real_pipe()
            descriptors.extend(pair)
            return pair

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with (
                patch("midivoice.cdp.os.name", "posix"),
                patch("midivoice.cdp.os.pipe", pipe),
                patch("midivoice.cdp.build_cdp_launch", return_value=([], {}, [])),
                patch(
                    "midivoice.cdp.subprocess.Popen",
                    side_effect=OSError("spawn failed"),
                ),
            ):
                with self.assertRaises(OSError):
                    await connection.start("fake", [], folder, folder / "log")
        for fd in descriptors:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertIsNone(connection.proc)
