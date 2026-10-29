from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import stat
import os
import tempfile
import unittest
from unittest.mock import patch

from midivoice import pulse_mic, pulse


class TestPulseMic(unittest.TestCase):
    def test_synthetic_input_pause_expires_and_can_resume(self):
        now = [20.0]
        control = pulse_mic.TemporaryInput(clock=lambda: now[0])
        self.assertFalse(control.paused())
        control.pause()
        now[0] = 29.9
        self.assertTrue(control.paused())
        now[0] = 30.0
        self.assertFalse(control.paused())
        control.pause()
        control.resume()
        self.assertFalse(control.paused())

    @unittest.skipUnless(hasattr(pulse_mic.signal, "SIGUSR1"), "POSIX test signals")
    def test_synthetic_input_signals_restore_handlers_after_error(self):
        import signal

        original = {n: signal.getsignal(n) for n in (signal.SIGUSR1, signal.SIGUSR2)}
        with self.assertRaisesRegex(RuntimeError, "probe failed"):
            with pulse_mic.temporary_input_signals() as paused:
                signal.raise_signal(signal.SIGUSR1)
                self.assertTrue(paused())
                signal.raise_signal(signal.SIGUSR2)
                self.assertFalse(paused())
                raise RuntimeError("probe failed")
        for number, handler in original.items():
            self.assertEqual(signal.getsignal(number), handler)

    def test_config_only_local_and_correct_16khz(self):
        cfg = pulse.pulse_config()
        self.assertIn("module-native-protocol-unix", cfg)
        self.assertIn("module-pipe-source", cfg)
        self.assertIn("rate=16000 channels=1", cfg)
        self.assertIn("source_name=midi_microphone", cfg)
        self.assertIn("set-default-source midi_microphone", cfg)
        self.assertNotIn("protocol-tcp", cfg)
        self.assertNotIn("auth-anonymous=1", cfg)

    def test_capture_command_matches_recorded_hardware(self):
        args = pulse_mic.capture_command()
        self.assertIn("midi_capture", args)
        self.assertIn("96000", args)
        self.assertIn("4", args)
        self.assertIn("raw", args)

    def build_root(self, temp):
        root = Path(temp)
        (root / "sbin").mkdir()
        (root / "sbin" / "apk").touch()
        (root / "tmp").mkdir()
        return root

    @patch("midivoice.pulse.os.chown", create=True)
    def test_setup_leaves_fifo_for_pulseaudio_and_writes_config(self, _chown):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_root(temp)
            result = pulse.setup(root)
            fifo = Path(result["mic_fifo"])
            self.assertFalse(
                fifo.exists()
            )  # Regression: PulseAudio must mkfifo itself.
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(fifo.parent.stat().st_mode), 0o700)
            cfg = root / "home/chatgpt/midi-pulse.pa"
            self.assertEqual(cfg.read_text(encoding="utf-8"), pulse.pulse_config())
            self.assertIn(
                "/tmp/midi-chatgpt-audio/midi-microphone.pcm",
                cfg.read_text(encoding="utf-8"),
            )
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
            pulse.setup(root)
            self.assertFalse(fifo.exists())
            self.assertEqual(
                (root.parent / "midi-mic-alsa.conf").read_text(encoding="utf-8"),
                pulse.isolated_alsa_config_text(),
            )

    @patch("midivoice.pulse.os.chown", create=True)
    @unittest.skipUnless(os.name == "posix", "POSIX FIFO permissions")
    def test_prepare_removes_stale_fifo_and_secure_restricts_new_fifo(self, _chown):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_root(temp)
            pulse.setup(root)
            fifo = pulse.fifo_path(root)
            # Test runner is not UID 1000, so mock only ownership inspection.
            import os

            os.mkfifo(fifo)
            os.mkfifo(pulse.speaker_fifo_path(root))
            info = fifo.lstat()
            reported = SimpleNamespace(st_mode=info.st_mode, st_uid=1000)
            with patch.object(Path, "lstat", return_value=reported):
                pulse.secure_fifo(root)
                self.assertEqual(stat.S_IMODE(fifo.stat().st_mode), 0o600)
                self.assertTrue(pulse.clear_stale_fifo(root))
            self.assertFalse(fifo.exists())
            self.assertFalse(pulse.speaker_fifo_path(root).exists())
            self.assertFalse(pulse.clear_stale_fifo(root))

    @patch("midivoice.pulse.os.chown", create=True)
    def test_reject_regular_file_as_fifo(self, _chown):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_root(temp)
            pulse.setup(root)
            fifo = pulse.fifo_path(root)
            fifo.write_text("attack")
            with self.assertRaisesRegex(RuntimeError, "not a FIFO"):
                pulse.setup(root)
            with self.assertRaisesRegex(RuntimeError, "non-FIFO"):
                pulse.clear_stale_fifo(root)
            self.assertEqual(fifo.read_text(encoding="utf-8"), "attack")

    @unittest.skipUnless(os.name == "posix", "POSIX symlinks")
    @patch("midivoice.pulse.os.chown", create=True)
    def test_reject_symlink_in_private_directory(self, _chown):
        with tempfile.TemporaryDirectory() as temp:
            root = self.build_root(temp)
            (root / "tmp" / pulse.FIFO_DIR).symlink_to("/tmp")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                pulse.setup(root)

    def test_alsa_capture_uses_private_config_not_global_includes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "alpine"
            root.mkdir()
            with patch.dict("os.environ", {"ALSA_CONFIG_PATH": "/etc/alsa/bad.conf"}):
                environment = pulse.capture_environment(root)
            cfg = root.parent / "midi-mic-alsa.conf"
            self.assertEqual(environment["ALSA_CONFIG_PATH"], str(cfg))
            self.assertEqual(
                cfg.read_text(encoding="utf-8"), pulse.isolated_alsa_config_text()
            )
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
            self.assertNotIn("include", cfg.read_text(encoding="utf-8"))
            self.assertNotIn("conf.d", cfg.read_text(encoding="utf-8"))
            args = pulse_mic.capture_command()
            self.assertEqual(args[args.index("-D") + 1], "midi_capture")
            self.assertIn("device 1", cfg.read_text(encoding="utf-8"))
            self.assertIn("card 0", cfg.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "POSIX symlinks")
    def test_alsa_config_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "alpine"
            root.mkdir()
            destination = Path(tmp) / "do-not-overwrite"
            destination.write_text("unchanged")
            (Path(tmp) / "midi-mic-alsa.conf").symlink_to(destination)
            with self.assertRaisesRegex(RuntimeError, "Refusing unsafe ALSA config"):
                pulse.capture_environment(root)
            self.assertEqual(destination.read_text(encoding="utf-8"), "unchanged")

    def test_native_sox_pipeline(self):
        args = pulse_mic.sox_command(channel=1, gain_db=24)
        self.assertIn("midivoice.alpine_exec", args)
        self.assertIn("sox", args)
        self.assertEqual(args[-7:], ["remix", "2", "gain", "24", "rate", "-l", "16000"])
        self.assertIn("96000", args)
        self.assertIn("16000", args)
        self.assertNotIn("--no-sandbox", args)

    def test_sox_rejects_invalid_channel_and_gain(self):
        for channel in (-1, 4):
            with self.assertRaises(ValueError):
                pulse_mic.sox_command(channel=channel)
        for gain in (-13, 37):
            with self.assertRaises(ValueError):
                pulse_mic.sox_command(gain_db=gain)

    def test_native_sox_installed_by_audio_script(self):
        script = (
            Path(__file__).resolve().parents[1] / "scripts/install-midi-audio.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("pulseaudio-utils sox", script)
        self.assertIn("usr/bin/sox", script)

    def test_smaller_vnc_default(self):
        script = (
            Path(__file__).resolve().parents[1] / "scripts/login-browser.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("800x600x24", script)
        self.assertIn("MIDI_VNC_SCREEN", script)

    def test_nonblocking_write_handles_backpressure(self):
        import os

        a, b = os.pipe()
        try:
            os.set_blocking(b, False)
            self.assertTrue(pulse_mic.write_audio_nonblocking(b, b"hello"))
            self.assertEqual(os.read(a, 5), b"hello")
            self.assertTrue(pulse_mic.write_audio_nonblocking(b, b""))
        finally:
            os.close(a)
            os.close(b)


if __name__ == "__main__":
    unittest.main()
