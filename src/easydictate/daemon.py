from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gio, GLib

from easydictate.core import (
    read_settings,
    resolve_daemon_log_path,
    resolve_hotkey,
    resolve_hotkey_mode,
    resolve_state_dir,
)
from easydictate.engine import persist_error, run_dictation_session


APP_ID = "com.easydictate.app"
PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
PORTAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"
REGISTRY_INTERFACE = "org.freedesktop.host.portal.Registry"
SHORTCUT_ID = "dictation"


@dataclass
class DaemonConfig:
    hotkey: str
    hotkey_mode: str
    language: str | None = None
    prompt: str | None = None
    autopaste: bool = True


class DictationDaemon:
    def __init__(
        self,
        *,
        config: DaemonConfig,
        state_dir: Path,
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.state_dir = state_dir
        self.logger = logger
        self.recording_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.audio_path: Path | None = None
        self.lock = threading.Lock()

    def handle_shortcut_activated(self, shortcut_id: str) -> None:
        if shortcut_id != SHORTCUT_ID:
            return
        if self.config.hotkey_mode == "hold":
            if not self.is_recording():
                self.start_recording()
            return
        if self.is_recording():
            self.stop_recording()
        else:
            self.start_recording()

    def handle_shortcut_deactivated(self, shortcut_id: str) -> None:
        if shortcut_id != SHORTCUT_ID:
            return
        if self.config.hotkey_mode != "hold":
            return
        if self.is_recording():
            self.stop_recording()

    def is_recording(self) -> bool:
        return self.recording_thread is not None and self.recording_thread.is_alive()

    def start_recording(self) -> None:
        with self.lock:
            if self.is_recording():
                return
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.audio_path = self.state_dir / f"capture-{int(time.time() * 1000)}.wav"
            self.stop_event = threading.Event()
            self.recording_thread = threading.Thread(target=self._recording_worker, daemon=True)
            self.recording_thread.start()
            self.logger.info("Recording started")

    def stop_recording(self) -> None:
        with self.lock:
            if self.stop_event is None:
                return
            self.stop_event.set()
            self.logger.info("Recording stop requested")

    def shutdown(self) -> None:
        self.stop_recording()

    def _recording_worker(self) -> None:
        stop_event = self.stop_event
        audio_path = self.audio_path
        try:
            if stop_event is None or audio_path is None:
                return
            result = run_dictation_session(
                state_dir=self.state_dir,
                audio_path=audio_path,
                stop_event=stop_event,
                language=self.config.language,
                prompt=self.config.prompt,
                autopaste=self.config.autopaste,
            )
            audio_path.unlink(missing_ok=True)
            summary = "Transcription pasted" if result.pasted else "Transcription copied to clipboard"
            self.logger.info("%s via %s", summary, result.backend)
        except Exception as exc:  # noqa: BLE001
            error_path = persist_error(self.state_dir, str(exc), audio_path)
            self.logger.exception("Dictation failed. Details: %s", error_path)
        finally:
            with self.lock:
                self.recording_thread = None
                self.stop_event = None
                self.audio_path = None


class GlobalShortcutsPortal:
    def __init__(self, logger: logging.Logger, state_dir: Path) -> None:
        self.logger = logger
        self.state_dir = state_dir
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.proxy = Gio.DBusProxy.new_sync(
            self.connection,
            Gio.DBusProxyFlags.NONE,
            None,
            PORTAL_BUS_NAME,
            PORTAL_OBJECT_PATH,
            PORTAL_INTERFACE,
            None,
        )
        self.unique_name = self.connection.get_unique_name()
        self.session_handle: str | None = None
        self.signal_ids: list[int] = []

    def start(
        self,
        *,
        app_id: str,
        preferred_trigger: str,
        on_activated: callable,
        on_deactivated: callable,
    ) -> list[dict[str, Any]]:
        self._register_app(app_id)
        self.session_handle = self._create_session()
        self._subscribe_shortcut_signals(on_activated=on_activated, on_deactivated=on_deactivated)
        metadata = self._load_metadata()
        force_bind = metadata.get("bound_hotkey") != preferred_trigger
        shortcuts = self._prepare_shortcuts(preferred_trigger=preferred_trigger, force_bind=force_bind)
        if force_bind and shortcuts:
            self._save_metadata({"bound_hotkey": preferred_trigger})
        return shortcuts

    def shutdown(self) -> None:
        for signal_id in self.signal_ids:
            self.connection.signal_unsubscribe(signal_id)
        self.signal_ids.clear()
        if not self.session_handle:
            return
        try:
            self.connection.call_sync(
                PORTAL_BUS_NAME,
                self.session_handle,
                SESSION_INTERFACE,
                "Close",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error:
            pass

    def write_desktop_entry(self, *, applications_dir: Path, exec_command: str) -> Path:
        applications_dir.mkdir(parents=True, exist_ok=True)
        desktop_path = applications_dir / f"{APP_ID}.desktop"
        desktop_path.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=EasyDictate",
                    f"Exec={exec_command}",
                    "Terminal=false",
                    "Categories=Utility;",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return desktop_path

    def _prepare_shortcuts(self, *, preferred_trigger: str, force_bind: bool) -> list[dict[str, Any]]:
        if not force_bind:
            shortcuts = self._list_shortcuts()
            if shortcuts:
                self.logger.info("Using portal shortcut %s", self._describe_shortcuts(shortcuts))
                return shortcuts
        shortcuts = self._bind_shortcuts(preferred_trigger)
        if not shortcuts:
            raise RuntimeError("The Global Shortcuts portal returned no active shortcuts.")
        self.logger.info("Bound portal shortcut %s", self._describe_shortcuts(shortcuts))
        return shortcuts

    def _create_session(self) -> str:
        handle_token = self._new_token()
        response = self._call_request(
            "CreateSession",
            GLib.Variant(
                "(a{sv})",
                (
                    {
                        "handle_token": GLib.Variant("s", handle_token),
                        "session_handle_token": GLib.Variant("s", self._new_token()),
                    },
                ),
            ),
            handle_token,
        )
        session_handle = str(response["session_handle"])
        self.logger.info("Created portal session %s", session_handle)
        return session_handle

    def _register_app(self, app_id: str) -> None:
        registry = Gio.DBusProxy.new_sync(
            self.connection,
            Gio.DBusProxyFlags.NONE,
            None,
            PORTAL_BUS_NAME,
            PORTAL_OBJECT_PATH,
            REGISTRY_INTERFACE,
            None,
        )
        try:
            registry.call_sync(
                "Register",
                GLib.Variant("(sa{sv})", (app_id, {})),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error as exc:
            message = exc.message or ""
            if "already associated with an application ID" not in message:
                raise
        self.logger.info("Registered app id %s with host portal registry", app_id)

    def _bind_shortcuts(self, preferred_trigger: str) -> list[dict[str, Any]]:
        if self.session_handle is None:
            raise RuntimeError("Portal session was not created.")
        handle_token = self._new_token()
        shortcut_data = [
            (
                SHORTCUT_ID,
                {
                    "description": GLib.Variant("s", "Toggle dictation recording"),
                    "preferred_trigger": GLib.Variant("s", preferred_trigger),
                },
            )
        ]
        response = self._call_request(
            "BindShortcuts",
            GLib.Variant(
                "(oa(sa{sv})sa{sv})",
                (
                    self.session_handle,
                    shortcut_data,
                    "",
                    {"handle_token": GLib.Variant("s", handle_token)},
                ),
            ),
            handle_token,
        )
        return self._unpack_shortcuts(response.get("shortcuts", []))

    def _list_shortcuts(self) -> list[dict[str, Any]]:
        if self.session_handle is None:
            raise RuntimeError("Portal session was not created.")
        handle_token = self._new_token()
        response = self._call_request(
            "ListShortcuts",
            GLib.Variant(
                "(oa{sv})",
                (
                    self.session_handle,
                    {"handle_token": GLib.Variant("s", handle_token)},
                ),
            ),
            handle_token,
        )
        return self._unpack_shortcuts(response.get("shortcuts", []))

    def _call_request(self, method: str, parameters: GLib.Variant, handle_token: str) -> dict[str, Any]:
        request_path = self._build_request_path(handle_token)
        response_data: dict[str, Any] = {}
        loop = GLib.MainLoop()

        def on_response(
            _connection: Gio.DBusConnection,
            _sender_name: str,
            _object_path: str,
            _interface_name: str,
            _signal_name: str,
            signal_parameters: GLib.Variant,
        ) -> None:
            response_code, results = signal_parameters.unpack()
            response_data["code"] = response_code
            response_data["results"] = results
            loop.quit()

        signal_id = self.connection.signal_subscribe(
            PORTAL_BUS_NAME,
            REQUEST_INTERFACE,
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
        )
        try:
            self.proxy.call_sync(method, parameters, Gio.DBusCallFlags.NONE, -1, None)
            loop.run()
        finally:
            self.connection.signal_unsubscribe(signal_id)
        response_code = int(response_data.get("code", 2))
        if response_code != 0:
            raise RuntimeError(f"Portal request {method} failed with response code {response_code}.")
        results = response_data.get("results", {})
        if not isinstance(results, dict):
            return {}
        return results

    def _subscribe_shortcut_signals(self, *, on_activated: callable, on_deactivated: callable) -> None:
        def activated_callback(
            _connection: Gio.DBusConnection,
            _sender_name: str,
            _object_path: str,
            _interface_name: str,
            _signal_name: str,
            parameters: GLib.Variant,
        ) -> None:
            session_handle, shortcut_id, _timestamp, _options = parameters.unpack()
            if session_handle == self.session_handle:
                on_activated(shortcut_id)

        def deactivated_callback(
            _connection: Gio.DBusConnection,
            _sender_name: str,
            _object_path: str,
            _interface_name: str,
            _signal_name: str,
            parameters: GLib.Variant,
        ) -> None:
            session_handle, shortcut_id, _timestamp, _options = parameters.unpack()
            if session_handle == self.session_handle:
                on_deactivated(shortcut_id)

        self.signal_ids.append(
            self.connection.signal_subscribe(
                PORTAL_BUS_NAME,
                PORTAL_INTERFACE,
                "Activated",
                PORTAL_OBJECT_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                activated_callback,
            )
        )
        self.signal_ids.append(
            self.connection.signal_subscribe(
                PORTAL_BUS_NAME,
                PORTAL_INTERFACE,
                "Deactivated",
                PORTAL_OBJECT_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                deactivated_callback,
            )
        )

    def _new_token(self) -> str:
        return "easydictate_" + uuid.uuid4().hex

    def _build_request_path(self, token: str) -> str:
        if self.unique_name is None:
            raise RuntimeError("Could not determine the D-Bus unique name.")
        sender = self.unique_name.lstrip(":").replace(".", "_")
        return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def _metadata_path(self) -> Path:
        return self.state_dir / "portal.json"

    def _load_metadata(self) -> dict[str, Any]:
        path = self._metadata_path()
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_metadata(self, data: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path().write_text(json.dumps(data), encoding="utf-8")

    def _describe_shortcuts(self, shortcuts: list[dict[str, Any]]) -> str:
        if not shortcuts:
            return "none"
        descriptions = [shortcut.get("trigger_description") or shortcut.get("id") or "unknown" for shortcut in shortcuts]
        return ", ".join(str(item) for item in descriptions)

    def _unpack_shortcuts(self, shortcuts: Any) -> list[dict[str, Any]]:
        unpacked: list[dict[str, Any]] = []
        for shortcut_id, values in shortcuts:
            if isinstance(values, dict):
                normalized = {key: value for key, value in values.items()}
            else:
                normalized = {}
            normalized["id"] = shortcut_id
            unpacked.append(normalized)
        return unpacked


def load_daemon_config(settings: dict[str, Any]) -> DaemonConfig:
    autopaste = settings.get("autopaste", True)
    if not isinstance(autopaste, bool):
        raise RuntimeError("Invalid autopaste setting. Expected true or false.")
    language = settings.get("language")
    prompt = settings.get("prompt")
    return DaemonConfig(
        hotkey=resolve_hotkey(settings),
        hotkey_mode=resolve_hotkey_mode(settings),
        language=str(language) if language is not None else None,
        prompt=str(prompt) if prompt is not None else None,
        autopaste=autopaste,
    )


def configure_logging(state_dir: Path) -> logging.Logger:
    state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("easydictate.daemon")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(resolve_daemon_log_path(state_dir), encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def resolve_applications_dir(env: dict[str, str] | None = None) -> Path:
    env = env or dict(os.environ)
    data_home = env.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "applications"
    home = env.get("HOME")
    if not home:
        return Path.home() / ".local" / "share" / "applications"
    return Path(home) / ".local" / "share" / "applications"


def main() -> None:
    state_dir = resolve_state_dir()
    logger = configure_logging(state_dir)
    loop = GLib.MainLoop()
    portal: GlobalShortcutsPortal | None = None
    daemon: DictationDaemon | None = None

    def shutdown(_signum: int, _frame: Any) -> None:
        logger.info("Shutdown requested")
        if daemon is not None:
            daemon.shutdown()
        if portal is not None:
            portal.shutdown()
        loop.quit()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    settings = read_settings()
    config = load_daemon_config(settings)
    logger.info("Starting daemon with hotkey=%s mode=%s", config.hotkey, config.hotkey_mode)
    daemon = DictationDaemon(config=config, state_dir=state_dir, logger=logger)
    portal = GlobalShortcutsPortal(logger=logger, state_dir=state_dir)
    portal.write_desktop_entry(
        applications_dir=resolve_applications_dir(),
        exec_command=f"{Path(__file__).resolve().parents[2] / '.venv' / 'bin' / 'easydictate'} daemon",
    )
    shortcuts = portal.start(
        app_id=APP_ID,
        preferred_trigger=config.hotkey,
        on_activated=daemon.handle_shortcut_activated,
        on_deactivated=daemon.handle_shortcut_deactivated,
    )
    logger.info("Portal ready with shortcuts: %s", portal._describe_shortcuts(shortcuts))
    loop.run()


if __name__ == "__main__":
    main()
