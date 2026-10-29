import signal
import subprocess
from threading import Event
import unittest
from unittest.mock import Mock, patch

from midivoice.processes import stop_signals, terminate, managed_process
from midivoice.pulse_speaker import write_all


class ProcessLifecycleTests(unittest.TestCase):
    def test_managed_process_cleans_group_on_exception(self):
        process = Mock(pid=42, stdin=None, stdout=None, stderr=None)
        process.poll.return_value = 0
        with (
            patch("midivoice.processes.os.name", "posix"),
            patch("midivoice.processes.os.killpg", create=True) as killpg,
            patch("midivoice.processes.signal.SIGKILL", 9, create=True),
            patch("midivoice.processes.subprocess.Popen", return_value=process),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                with managed_process(["fake"]):
                    raise RuntimeError("failed")
        self.assertEqual(
            [call.args for call in killpg.call_args_list],
            [(42, signal.SIGTERM), (42, 9)],
        )

    def test_audio_shutdown_restores_signal_handlers(self):
        previous = {signal.SIGTERM: object(), signal.SIGINT: object()}
        installed = {}

        def install(number, handler):
            installed[number] = handler
            return previous[number]

        with patch("midivoice.processes.signal.signal", side_effect=install):
            with stop_signals() as stopping:
                installed[signal.SIGTERM](signal.SIGTERM, None)
                self.assertTrue(stopping.is_set())
            self.assertEqual(installed, previous)

    def test_stuck_process_is_killed_and_pipes_closed(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fake", 2), 0]
        terminate(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close.assert_called_once()

    def test_backpressured_speaker_can_stop(self):
        stopping = Event()
        with (
            patch(
                "midivoice.pulse_speaker.os.write", side_effect=BlockingIOError()
            ) as write,
            patch(
                "midivoice.pulse_speaker.select.select",
                side_effect=lambda *args: stopping.set(),
            ),
        ):
            write_all(42, b"pcm", stopping)
        write.assert_called_once()
