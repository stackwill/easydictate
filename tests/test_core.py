import os
import tempfile
import unittest
from pathlib import Path

from easydictate.core import (
    build_record_backend_order,
    build_transcription_request,
    choose_paste_command,
    choose_record_backend,
    format_process_error,
    format_missing_recording_error,
    resolve_daemon_log_path,
    resolve_hotkey,
    resolve_hotkey_mode,
    resolve_error_report_path,
    resolve_state_dir,
    resolve_transcript_report_path,
)


class ResolveStateDirTests(unittest.TestCase):
    def test_prefers_xdg_state_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = resolve_state_dir({"XDG_STATE_HOME": tmp})
            self.assertEqual(path, Path(tmp) / "easydictate")

    def test_falls_back_to_home_local_state(self) -> None:
        path = resolve_state_dir({"HOME": "/tmp/example-home"})
        self.assertEqual(path, Path("/tmp/example-home/.local/state/easydictate"))


class ChoosePasteCommandTests(unittest.TestCase):
    def test_prefers_xdotool_when_x11_is_available(self) -> None:
        env = {"DISPLAY": ":0"}
        which = lambda name: f"/usr/bin/{name}" if name == "xdotool" else None
        self.assertEqual(
            choose_paste_command(env, which),
            ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"],
        )

    def test_uses_wtype_on_non_gnome_wayland_when_available(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        which = lambda name: f"/usr/bin/{name}" if name == "wtype" else None
        self.assertEqual(
            choose_paste_command(env, which),
            ["wtype", "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"],
        )

    def test_prefers_ydotool_on_gnome_wayland(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "GNOME"}
        which = lambda name: f"/usr/bin/{name}" if name in {"wtype", "ydotool"} else None
        self.assertEqual(
            choose_paste_command(env, which),
            ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
        )

    def test_returns_none_without_supported_tool(self) -> None:
        self.assertIsNone(choose_paste_command({}, lambda _: None))


class ChooseRecordBackendTests(unittest.TestCase):
    def test_respects_explicit_backend_override(self) -> None:
        which = lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "parecord", "pw-record", "arecord"} else None
        self.assertEqual(choose_record_backend(which, preferred="arecord"), "arecord")

    def test_prefers_ffmpeg_when_available(self) -> None:
        which = lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "parecord", "pw-record", "arecord"} else None
        self.assertEqual(choose_record_backend(which), "ffmpeg")

    def test_falls_back_to_parecord(self) -> None:
        which = lambda name: f"/usr/bin/{name}" if name == "parecord" else None
        self.assertEqual(choose_record_backend(which), "parecord")

    def test_uses_sounddevice_last(self) -> None:
        self.assertEqual(choose_record_backend(lambda _: None), "sounddevice")


class BuildRecordBackendOrderTests(unittest.TestCase):
    def test_moves_preferred_backend_to_front(self) -> None:
        which = lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "parecord", "pw-record", "arecord"} else None
        self.assertEqual(
            build_record_backend_order(which, preferred="arecord"),
            ["arecord", "ffmpeg", "parecord", "pw-record", "sounddevice"],
        )

    def test_uses_detected_order_when_no_preference(self) -> None:
        which = lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "parecord", "pw-record", "arecord"} else None
        self.assertEqual(
            build_record_backend_order(which),
            ["ffmpeg", "parecord", "pw-record", "arecord", "sounddevice"],
        )


class FormatProcessErrorTests(unittest.TestCase):
    def test_uses_stderr_when_present(self) -> None:
        self.assertEqual(
            format_process_error("arecord", 1, "audio open error: Device or resource busy"),
            "arecord failed: audio open error: Device or resource busy",
        )

    def test_falls_back_to_exit_code(self) -> None:
        self.assertEqual(format_process_error("arecord", 1, ""), "arecord exited with code 1")

    def test_formats_missing_recording_error(self) -> None:
        audio_path = Path("/tmp/missing.wav")
        self.assertEqual(
            format_missing_recording_error("pw-record", audio_path),
            "pw-record did not produce a recording file at /tmp/missing.wav",
        )


class ReportPathTests(unittest.TestCase):
    def test_resolves_error_report_path_under_state_dir(self) -> None:
        state_dir = Path("/tmp/easydictate")
        self.assertEqual(resolve_error_report_path(state_dir), state_dir / "last_error.txt")

    def test_resolves_daemon_log_path_under_state_dir(self) -> None:
        state_dir = Path("/tmp/easydictate")
        self.assertEqual(resolve_daemon_log_path(state_dir), state_dir / "daemon.log")

    def test_resolves_transcript_report_path_under_state_dir(self) -> None:
        state_dir = Path("/tmp/easydictate")
        self.assertEqual(resolve_transcript_report_path(state_dir), state_dir / "last_transcript.txt")


class HotkeyConfigTests(unittest.TestCase):
    def test_uses_default_hotkey_when_missing(self) -> None:
        self.assertEqual(resolve_hotkey({}), "CTRL+bracketright")

    def test_uses_configured_hotkey(self) -> None:
        self.assertEqual(resolve_hotkey({"hotkey": "ALT+F10"}), "ALT+F10")

    def test_uses_default_hotkey_mode_when_missing(self) -> None:
        self.assertEqual(resolve_hotkey_mode({}), "toggle")

    def test_accepts_hold_hotkey_mode(self) -> None:
        self.assertEqual(resolve_hotkey_mode({"hotkey_mode": "hold"}), "hold")

    def test_rejects_invalid_hotkey_mode(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "hotkey_mode"):
            resolve_hotkey_mode({"hotkey_mode": "tap"})


class BuildTranscriptionRequestTests(unittest.TestCase):
    def test_builds_expected_payload(self) -> None:
        request = build_transcription_request(
            api_key="secret",
            audio_path=Path("/tmp/clip.wav"),
            language="en",
            prompt="trim filler words",
        )

        self.assertEqual(request["url"], "https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(request["headers"]["Accept"], "application/json")
        self.assertEqual(request["headers"]["User-Agent"], "EasyDictate/0.1")
        self.assertEqual(request["data"]["model"], "whisper-large-v3-turbo")
        self.assertEqual(request["data"]["language"], "en")
        self.assertEqual(request["data"]["prompt"], "trim filler words")
        self.assertEqual(request["files"]["file"][0], "clip.wav")


if __name__ == "__main__":
    unittest.main()
