import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easydictate.daemon import APP_ID, DaemonConfig, DictationDaemon, GlobalShortcutsPortal


class DictationDaemonShortcutTests(unittest.TestCase):
    def build_daemon(self, mode: str = "toggle") -> DictationDaemon:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return DictationDaemon(
            config=DaemonConfig(hotkey="CTRL+bracketright", hotkey_mode=mode),
            state_dir=Path(tmp.name),
            logger=mock.Mock(),
        )

    def test_toggle_mode_activation_starts_when_idle(self) -> None:
        daemon = self.build_daemon("toggle")

        with mock.patch.object(daemon, "start_recording") as start_recording:
            daemon.handle_shortcut_activated("dictation")

        start_recording.assert_called_once_with()

    def test_toggle_mode_activation_stops_when_recording(self) -> None:
        daemon = self.build_daemon("toggle")
        daemon.stop_event = mock.Mock()
        daemon.recording_thread = mock.Mock()

        with mock.patch.object(daemon, "is_recording", return_value=True):
            daemon.handle_shortcut_activated("dictation")

        daemon.stop_event.set.assert_called_once_with()

    def test_toggle_mode_deactivation_is_ignored(self) -> None:
        daemon = self.build_daemon("toggle")

        with mock.patch.object(daemon, "stop_recording") as stop_recording:
            daemon.handle_shortcut_deactivated("dictation")

        stop_recording.assert_not_called()

    def test_hold_mode_activation_starts_when_idle(self) -> None:
        daemon = self.build_daemon("hold")

        with mock.patch.object(daemon, "start_recording") as start_recording:
            daemon.handle_shortcut_activated("dictation")

        start_recording.assert_called_once_with()

    def test_hold_mode_deactivation_stops_when_recording(self) -> None:
        daemon = self.build_daemon("hold")

        with mock.patch.object(daemon, "is_recording", return_value=True):
            with mock.patch.object(daemon, "stop_recording") as stop_recording:
                daemon.handle_shortcut_deactivated("dictation")

        stop_recording.assert_called_once_with()

    def test_ignores_other_shortcut_ids(self) -> None:
        daemon = self.build_daemon("toggle")

        with mock.patch.object(daemon, "start_recording") as start_recording:
            daemon.handle_shortcut_activated("other")

        start_recording.assert_not_called()


class GlobalShortcutsPortalRegistrationTests(unittest.TestCase):
    def test_register_app_calls_host_registry(self) -> None:
        portal = object.__new__(GlobalShortcutsPortal)
        portal.connection = mock.Mock()
        portal.logger = mock.Mock()
        portal.state_dir = Path("/tmp/easydictate")
        portal.proxy = mock.Mock()

        registry_proxy = mock.Mock()
        with mock.patch("easydictate.daemon.Gio.DBusProxy.new_sync", return_value=registry_proxy):
            portal._register_app(APP_ID)

        registry_proxy.call_sync.assert_called_once()

    def test_write_desktop_entry_creates_matching_desktop_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            portal = object.__new__(GlobalShortcutsPortal)
            portal.connection = mock.Mock()
            portal.logger = mock.Mock()
            portal.proxy = mock.Mock()
            portal.state_dir = Path(tmp)

            desktop_path = portal.write_desktop_entry(
                applications_dir=Path(tmp),
                exec_command="/tmp/easydictate daemon",
            )

            self.assertEqual(desktop_path.name, APP_ID + ".desktop")
            content = desktop_path.read_text(encoding="utf-8")
            self.assertIn("Exec=/tmp/easydictate daemon", content)
            self.assertIn("Name=EasyDictate", content)


if __name__ == "__main__":
    unittest.main()
