import asyncio
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from midivoice.cli import main


class CLITests(unittest.TestCase):
    def test_acceptance_handshake_keeps_navigation_output_as_json(self):
        async def reply(action, socket, *, progress, argument):
            await progress({"event": "progress", "stage": "accepted"})
            return {"ok": True, "workspace_mode": "work"}

        output = io.StringIO()
        with (
            patch("midivoice.cli.request_action", side_effect=reply),
            redirect_stdout(output),
        ):
            self.assertEqual(main(["mode-work"]), 0)
        self.assertEqual(json.loads(output.getvalue())["workspace_mode"], "work")

    def test_speech_parser_does_not_require_a_running_daemon(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(
                main(["speech-parse", "Алиса, открой ChatGPT", "--wake-verified"]), 0
            )
        self.assertEqual(json.loads(output.getvalue()), {"action": "open"})

    def test_invalid_config_returns_clear_error(self):
        error = io.StringIO()
        with (
            patch.dict(os.environ, {"MIDI_VOICE_WAIT_SECONDS": "wrong"}),
            redirect_stderr(error),
        ):
            self.assertEqual(main(["doctor"]), 1)
        self.assertIn("MIDI_VOICE_WAIT_SECONDS", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_daemon_sigterm_cancellation_is_normal_exit(self):
        with patch(
            "midivoice.cli.serve", new=AsyncMock(side_effect=asyncio.CancelledError())
        ):
            self.assertEqual(main(["serve"]), 0)

    def test_daemon_error_is_a_nonzero_exit(self):
        with (
            patch(
                "midivoice.cli.serve",
                new=AsyncMock(side_effect=RuntimeError("already running")),
            ),
            redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(main(["serve"]), 1)
        self.assertIn("already running", error.getvalue())
