import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk

from easydictate import gui


class GuiHotkeyTests(unittest.TestCase):
    def build_app(self) -> gui.EasyDictateApplication:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch("easydictate.gui.resolve_state_dir", return_value=Path(tmp.name)):
            with mock.patch("easydictate.gui.read_settings", return_value={}):
                return gui.EasyDictateApplication()

    def test_toggle_recording_starts_when_idle(self) -> None:
        app = self.build_app()
        with mock.patch.object(app, "_start_recording") as start_recording:
            app.worker = None
            app._toggle_recording()

        start_recording.assert_called_once_with()

    def test_toggle_recording_stops_when_worker_is_running(self) -> None:
        app = self.build_app()
        app.worker = mock.Mock()
        app.worker.is_alive.return_value = True
        app.stop_event = mock.Mock()
        with mock.patch.object(app, "_refresh_ui") as refresh_ui:
            app._toggle_recording()

        app.stop_event.set.assert_called_once_with()
        self.assertEqual(app.status_text, "Stopping recording…")
        refresh_ui.assert_called_once_with()

    def test_start_hotkey_listener_marks_listener_as_active(self) -> None:
        app = self.build_app()
        app._configure_hotkey_status()

        self.assertEqual(app.hotkey_status_text, "Hotkey Ctrl+]: window-only (focus required)")

    def test_start_hotkey_listener_marks_listener_as_unavailable(self) -> None:
        app = self.build_app()
        with mock.patch.object(app, "_toggle_recording") as toggle_recording:
            handled = app._handle_window_keypress(
                None,
                Gdk.KEY_bracketright,
                0,
                Gdk.ModifierType.CONTROL_MASK,
            )

        self.assertTrue(handled)
        self.assertEqual(app.hotkey_status_text, "Hotkey Ctrl+]: detected 1 time(s)")
        toggle_recording.assert_called_once_with()

    def test_handle_hotkey_on_main_thread_records_detection(self) -> None:
        app = self.build_app()
        handled = app._handle_window_keypress(
            None,
            Gdk.KEY_a,
            0,
            Gdk.ModifierType.CONTROL_MASK,
        )

        self.assertFalse(handled)
        self.assertEqual(app.hotkey_trigger_count, 0)

    def test_refresh_ui_updates_hotkey_label(self) -> None:
        app = self.build_app()
        app.hotkey_label = mock.Mock()
        app.hotkey_status_text = "Hotkey Ctrl+]: listening"

        app._refresh_ui()

        app.hotkey_label.set_label.assert_called_once_with("Hotkey Ctrl+]: listening")


if __name__ == "__main__":
    unittest.main()
