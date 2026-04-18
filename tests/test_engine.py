import tempfile
import unittest
from pathlib import Path
from unittest import mock
import subprocess

from easydictate import engine


class RunDictationSessionTests(unittest.TestCase):
    def test_passes_preferred_source_to_record_microphone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            audio_path = state_dir / "capture.wav"
            audio_path.write_bytes(b"RIFFdemo")
            stop_event = mock.Mock()

            with mock.patch(
                "easydictate.engine.read_settings",
                return_value={
                    "GROQ_API_KEY": "secret",
                    "record_source": "alsa_input.usb-headset",
                },
            ):
                with mock.patch("easydictate.engine.record_microphone", return_value="ffmpeg") as record_mock:
                    with mock.patch("easydictate.engine.transcribe_audio", return_value="hello world"):
                        with mock.patch("easydictate.engine.copy_to_clipboard"):
                            with mock.patch("easydictate.engine.autopaste_text", return_value=False):
                                engine.run_dictation_session(
                                    state_dir=state_dir,
                                    audio_path=audio_path,
                                    stop_event=stop_event,
                                )

            self.assertEqual(record_mock.call_args.kwargs["preferred_source"], "alsa_input.usb-headset")

    def test_raises_clear_error_when_backend_produces_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            audio_path = state_dir / "missing.wav"
            stop_event = mock.Mock()
            stop_event.is_set.return_value = True

            with mock.patch("easydictate.engine.read_settings", return_value={"GROQ_API_KEY": "secret"}):
                with mock.patch("easydictate.engine.record_microphone", return_value="pw-record"):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        r"pw-record did not produce a recording file",
                    ):
                        engine.run_dictation_session(
                            state_dir=state_dir,
                            audio_path=audio_path,
                            stop_event=stop_event,
                        )

    def test_autopaste_failure_does_not_fail_dictation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            audio_path = state_dir / "capture.wav"
            audio_path.write_bytes(b"RIFFdemo")
            stop_event = mock.Mock()

            with mock.patch("easydictate.engine.read_settings", return_value={"GROQ_API_KEY": "secret"}):
                with mock.patch("easydictate.engine.record_microphone", return_value="ffmpeg"):
                    with mock.patch("easydictate.engine.transcribe_audio", return_value="hello world"):
                        with mock.patch("easydictate.engine.copy_to_clipboard"):
                            with mock.patch(
                                "easydictate.engine.autopaste_text",
                                side_effect=RuntimeError("ydotool failed"),
                            ):
                                result = engine.run_dictation_session(
                                    state_dir=state_dir,
                                    audio_path=audio_path,
                                    stop_event=stop_event,
                                )

            self.assertEqual(result.text, "hello world")
            self.assertFalse(result.pasted)


class RecordMicrophoneTests(unittest.TestCase):
    def test_falls_back_when_first_backend_produces_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "capture.wav"
            stop_event = mock.Mock()

            def write_file(path: Path, _stop_event: object) -> None:
                path.write_bytes(b"RIFFdemo")

            with mock.patch("easydictate.engine.build_record_backend_order", return_value=["pw-record", "arecord"]):
                with mock.patch("easydictate.engine.record_with_pw_record", return_value=None):
                    with mock.patch("easydictate.engine.record_with_arecord", side_effect=write_file):
                        backend = engine.record_microphone(audio_path, stop_event, preferred_backend="pw-record")

            self.assertEqual(backend, "arecord")
            self.assertTrue(audio_path.exists())


class AutoPasteTests(unittest.TestCase):
    def test_logs_and_returns_false_when_wtype_lacks_virtual_keyboard_support(self) -> None:
        error = subprocess.CalledProcessError(
            1,
            ["wtype", "-M", "ctrl", "v"],
            stderr="Compositor does not support the virtual keyboard protocol",
        )
        with mock.patch("easydictate.engine.choose_paste_command", return_value=["wtype", "-M", "ctrl", "v"]):
            with mock.patch("easydictate.engine.time.sleep"):
                with mock.patch("easydictate.engine.LOGGER") as logger_mock:
                    with mock.patch("easydictate.engine.subprocess.run", side_effect=error):
                        self.assertFalse(engine.autopaste_text())
        logger_mock.warning.assert_called_once()

    def test_retries_ydotool_connection_refused(self) -> None:
        error = subprocess.CalledProcessError(
            1,
            ["ydotool", "key"],
            stderr="failed to connect socket `/run/user/1000/.ydotool_socket': Connection refused",
        )
        with mock.patch("easydictate.engine.choose_paste_command", return_value=["ydotool", "key"]):
            with mock.patch("easydictate.engine.time.sleep"):
                with mock.patch(
                    "easydictate.engine.subprocess.run",
                    side_effect=[error, mock.Mock(returncode=0)],
                ) as run_mock:
                    self.assertTrue(engine.autopaste_text())
        self.assertEqual(run_mock.call_count, 2)

    def test_returns_false_when_no_paste_command(self) -> None:
        with mock.patch("easydictate.engine.choose_paste_command", return_value=None):
            self.assertFalse(engine.autopaste_text())


if __name__ == "__main__":
    unittest.main()
