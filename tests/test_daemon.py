import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gi.repository import GLib

from easydictate.daemon import APP_ID, PORTAL_INTERFACE, STALE_SHORTCUT_TIMEOUT_MS, DaemonConfig, DictationDaemon, GlobalShortcutsPortal, PortalUnavailableError


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
        self.assertTrue(daemon.stop_requested)

    def test_toggle_mode_deactivation_is_ignored(self) -> None:
        daemon = self.build_daemon("toggle")

        with mock.patch.object(daemon, "stop_recording") as stop_recording:
            daemon.handle_shortcut_activated("dictation")
            daemon.handle_shortcut_deactivated("dictation")

        stop_recording.assert_not_called()
        self.assertFalse(daemon.shortcut_active)

    def test_toggle_mode_repeated_activation_before_release_is_ignored(self) -> None:
        daemon = self.build_daemon("toggle")

        with mock.patch.object(daemon, "start_recording") as start_recording:
            daemon.handle_shortcut_activated("dictation")
            daemon.handle_shortcut_activated("dictation")

        start_recording.assert_called_once_with()

    def test_toggle_mode_reactivation_after_release_is_allowed(self) -> None:
        daemon = self.build_daemon("toggle")

        with mock.patch.object(daemon, "start_recording") as start_recording:
            daemon.handle_shortcut_activated("dictation")
            daemon.handle_shortcut_deactivated("dictation")
            daemon.handle_shortcut_activated("dictation")

        self.assertEqual(start_recording.call_count, 2)

    def test_toggle_mode_clears_stale_active_state_when_deactivation_was_missed(self) -> None:
        daemon = self.build_daemon("toggle")
        daemon.shortcut_active = True
        daemon.last_activation_timestamp = 1000

        with mock.patch.object(daemon, "is_recording", return_value=False):
            with mock.patch.object(daemon, "start_recording") as start_recording:
                daemon.handle_shortcut_activated("dictation", 1000 + STALE_SHORTCUT_TIMEOUT_MS + 1)

        start_recording.assert_called_once_with()

    def test_toggle_mode_activation_is_ignored_while_stop_is_in_progress(self) -> None:
        daemon = self.build_daemon("toggle")
        daemon.stop_requested = True

        with mock.patch.object(daemon, "start_recording") as start_recording:
            daemon.handle_shortcut_activated("dictation", 2000)

        start_recording.assert_not_called()

    def test_stop_recording_ignores_duplicate_requests(self) -> None:
        daemon = self.build_daemon("toggle")
        daemon.stop_event = mock.Mock()

        daemon.stop_recording()
        daemon.stop_recording()

        daemon.stop_event.set.assert_called_once_with()

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

    def test_register_app_ignores_missing_host_registry_interface(self) -> None:
        portal = object.__new__(GlobalShortcutsPortal)
        portal.connection = mock.Mock()
        portal.logger = mock.Mock()
        portal.state_dir = Path("/tmp/easydictate")
        portal.proxy = mock.Mock()

        registry_proxy = mock.Mock()
        registry_proxy.call_sync.side_effect = GLib.Error(
            f'GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: No such interface "org.freedesktop.host.portal.Registry"'
        )
        with mock.patch("easydictate.daemon.Gio.DBusProxy.new_sync", return_value=registry_proxy):
            portal._register_app(APP_ID)

        registry_proxy.call_sync.assert_called_once()

    def test_describe_portal_error_maps_missing_global_shortcuts_interface(self) -> None:
        portal = object.__new__(GlobalShortcutsPortal)
        error = GLib.Error(
            f'GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: No such interface "{PORTAL_INTERFACE}"'
        )

        described = portal._describe_portal_error(error)

        self.assertIsInstance(described, PortalUnavailableError)
        self.assertIn("GlobalShortcuts", str(described))


if __name__ == "__main__":
    unittest.main()
