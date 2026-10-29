import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from midivoice import control, quasar_ipc
from midivoice.config import Settings


def reader_for(*messages):
    reader = asyncio.StreamReader()
    for message in messages:
        reader.feed_data(
            message if isinstance(message, bytes) else control.encode_message(message)
        )
    reader.feed_eof()
    return reader


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class ControlTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX controller lease")
    async def test_controller_lease_is_exclusive_and_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sock"
            with control.controller_lease(path):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with control.controller_lease(path):
                        self.fail("second lease acquired")
            with control.controller_lease(path):
                pass

    async def test_cli_and_quasar_wait_for_final_response(self):
        for requester in (control.request_action, quasar_ipc.send_action):
            writer = Writer()
            reader = reader_for(
                {"event": "progress", "elapsed_s": 30},
                {"event": "progress", "elapsed_s": 60},
                {"ok": True, "mode": "voice"},
            )
            with patch(
                "midivoice.control.asyncio.open_unix_connection",
                create=True,
                new=AsyncMock(return_value=(reader, writer)),
            ):
                reply = await requester("open", "/fake.sock")
            self.assertEqual(reply, {"ok": True, "mode": "voice"})
            self.assertTrue(writer.closed)
            self.assertEqual(json.loads(writer.data), {"action": "open"})

    async def test_malformed_reply_closes_connection(self):
        for message in (
            [],
            {"ok": "yes"},
            b"{bad}\n",
            b"{}",
            b"x" * (control.MAX_REPLY + 1) + b"\n",
        ):
            writer = Writer()
            with patch(
                "midivoice.control.asyncio.open_unix_connection",
                create=True,
                new=AsyncMock(return_value=(reader_for(message), writer)),
            ):
                with self.assertRaises(ValueError):
                    await control.request_action("status", "/fake.sock")
            self.assertTrue(writer.closed)

    async def test_reject_invalid_command_before_dispatch(self):
        for message in (
            [],
            {"action": "delete"},
            {"action": ["open"]},
            {"action": "project"},
            {"action": "project", "argument": ["bad"]},
            {"action": "mode-work", "argument": "unexpected"},
            b"{bad}\n",
            b"{}",
        ):
            controller = AsyncMock()
            writer = Writer()
            await control.handle_client(reader_for(message), writer, controller)
            self.assertFalse(json.loads(writer.data)["ok"])
            controller.handle.assert_not_awaited()
            self.assertTrue(writer.closed)

    async def test_project_argument_is_transported_without_becoming_an_action(self):
        writer = Writer()
        with patch(
            "midivoice.control.asyncio.open_unix_connection",
            create=True,
            new=AsyncMock(return_value=(reader_for({"ok": True}), writer)),
        ):
            await control.request_action("project", "/fake.sock", argument='Home "one"')
        self.assertEqual(
            json.loads(writer.data), {"action": "project", "argument": 'Home "one"'}
        )

    async def test_progress_callback_and_result(self):
        writer = Writer()
        progress = AsyncMock()
        event = {"event": "progress", "elapsed_s": 1}
        with patch(
            "midivoice.control.asyncio.open_unix_connection",
            create=True,
            new=AsyncMock(return_value=(reader_for(event, {"ok": False}), writer)),
        ):
            result = await control.request_action(
                "open", "/fake.sock", progress=progress
            )
        progress.assert_awaited_once_with(event)
        self.assertFalse(result["ok"])

    async def test_disconnected_client_does_not_abort_startup(self):
        writer = Writer()
        writer.write = lambda data: (_ for _ in ()).throw(BrokenPipeError())
        finished = []

        async def handle(action, progress):
            await progress({"event": "progress"})
            finished.append(action)
            return {"ok": True}

        await control.handle_client(
            reader_for({"action": "open"}),
            writer,
            type("Controller", (), {"handle": staticmethod(handle)})(),
        )
        self.assertEqual(finished, ["open"])

    async def test_socket_path_never_overwrites_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sock"
            path.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "non-socket"):
                await control.prepare_socket(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "keep")

    @unittest.skipUnless(os.name == "posix", "POSIX Unix sockets")
    async def test_real_daemon_progress_and_shutdown(self):
        with tempfile.TemporaryDirectory(prefix="mv-") as directory:
            path = Path(directory)
            settings = Settings(state_dir=path, profile_dir=path / "profile")
            controller = AsyncMock()

            async def handle(action, progress=None):
                if progress:
                    await progress({"event": "progress", "elapsed_s": 1})
                return {"ok": True, "action": action}

            controller.handle.side_effect = handle
            task = asyncio.create_task(
                control.serve(settings=settings, controller=controller)
            )
            try:
                async with asyncio.timeout(3):
                    while not settings.socket_path.exists():
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
                self.assertEqual(
                    (await quasar_ipc.send_action("open", str(settings.socket_path)))[
                        "action"
                    ],
                    "open",
                )
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    await control.prepare_socket(settings.socket_path)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.assertFalse(settings.socket_path.exists())
            self.assertEqual(controller.handle.await_args.args, ("close",))
