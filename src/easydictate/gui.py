from __future__ import annotations

import queue
import time
from pathlib import Path
from threading import Event, Thread

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk

from easydictate.core import (
    read_settings,
    resolve_error_report_path,
    resolve_state_dir,
    resolve_transcript_report_path,
)
from easydictate.engine import DictationResult, autopaste_text, copy_to_clipboard, persist_error, run_dictation_session


class EasyDictateApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.easydictate.app")
        self.state_dir = resolve_state_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        settings = read_settings()

        self.status_text = "Ready"
        self.error_text = ""
        self.language_text = str(settings.get("language", ""))
        self.autopaste_enabled = True
        self.hotkey_status_text = "Hotkey Ctrl+]: starting"
        self.hotkey_trigger_count = 0
        self.events: queue.Queue[tuple[str, object, object, object]] = queue.Queue()
        self.worker: Thread | None = None
        self.stop_event: Event | None = None
        self.current_audio_path: Path | None = None
        self.pending_language: str | None = None
        self.pending_autopaste = True

        self.window: Gtk.ApplicationWindow | None = None
        self.status_label: Gtk.Label | None = None
        self.hotkey_label: Gtk.Label | None = None
        self.language_entry: Gtk.Entry | None = None
        self.autopaste_check: Gtk.CheckButton | None = None
        self.toggle_button: Gtk.Button | None = None
        self.text_buffer: Gtk.TextBuffer | None = None
        self.error_buffer: Gtk.TextBuffer | None = None
        self.error_scroller: Gtk.ScrolledWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = Gtk.ApplicationWindow(application=self)
            self.window.set_title("EasyDictate")
            self.window.set_default_size(760, 520)
            self.window.set_child(self._build_ui())
            self.window.connect("close-request", self._on_close_request)
            controller = Gtk.EventControllerKey()
            controller.connect("key-pressed", self._handle_window_keypress)
            self.window.add_controller(controller)
            GLib.timeout_add(100, self._drain_events)
            self._configure_hotkey_status()
            self._load_reports()
            self._refresh_ui()
        self.window.present()

    def _build_ui(self) -> Gtk.Widget:
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        container.set_margin_top(16)
        container.set_margin_bottom(16)
        container.set_margin_start(16)
        container.set_margin_end(16)

        title = Gtk.Label(label="EasyDictate")
        title.set_xalign(0)
        title.add_css_class("title-2")
        container.append(title)

        subtitle = Gtk.Label(
            label="Start recording, stop when finished, then review or paste the transcript below."
        )
        subtitle.set_xalign(0)
        subtitle.set_wrap(True)
        container.append(subtitle)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.toggle_button = Gtk.Button(label="Start Recording")
        self.toggle_button.connect("clicked", self._on_toggle_clicked)
        controls.append(self.toggle_button)

        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self._on_copy_clicked)
        controls.append(copy_button)

        copy_error_button = Gtk.Button(label="Copy Error")
        copy_error_button.connect("clicked", self._on_copy_error_clicked)
        controls.append(copy_error_button)

        paste_button = Gtk.Button(label="Paste")
        paste_button.connect("clicked", self._on_paste_clicked)
        controls.append(paste_button)

        clear_button = Gtk.Button(label="Clear")
        clear_button.connect("clicked", self._on_clear_clicked)
        controls.append(clear_button)

        self.autopaste_check = Gtk.CheckButton(label="Auto-paste after transcription")
        self.autopaste_check.set_active(True)
        controls.append(self.autopaste_check)
        container.append(controls)

        options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        language_label = Gtk.Label(label="Language")
        language_label.set_xalign(0)
        options.append(language_label)

        self.language_entry = Gtk.Entry()
        self.language_entry.set_text(self.language_text)
        self.language_entry.set_width_chars(10)
        options.append(self.language_entry)
        container.append(options)

        self.status_label = Gtk.Label(label=self.status_text)
        self.status_label.set_xalign(0)
        container.append(self.status_label)

        self.hotkey_label = Gtk.Label(label=self.hotkey_status_text)
        self.hotkey_label.set_xalign(0)
        self.hotkey_label.add_css_class("dim-label")
        container.append(self.hotkey_label)

        self.error_scroller = Gtk.ScrolledWindow()
        self.error_scroller.set_min_content_height(96)
        self.error_scroller.set_visible(False)

        error_view = Gtk.TextView()
        error_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        error_view.set_editable(False)
        error_view.set_cursor_visible(True)
        self.error_buffer = error_view.get_buffer()
        self.error_scroller.set_child(error_view)
        container.append(self.error_scroller)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)

        text_view = Gtk.TextView()
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_vexpand(True)
        self.text_buffer = text_view.get_buffer()
        scroller.set_child(text_view)
        container.append(scroller)

        return container

    def _on_toggle_clicked(self, _button: Gtk.Button) -> None:
        self._toggle_recording()

    def _on_copy_clicked(self, _button: Gtk.Button) -> None:
        text = self._current_text()
        if not text:
            self.status_text = "Nothing to copy"
            self._refresh_ui()
            return
        try:
            copy_to_clipboard(text)
        except Exception as exc:  # noqa: BLE001
            self.status_text = "Copy failed"
            self.error_text = str(exc)
            self._refresh_ui()
            return
        self.status_text = "Copied to clipboard"
        self.error_text = ""
        self._refresh_ui()

    def _on_paste_clicked(self, _button: Gtk.Button) -> None:
        text = self._current_text()
        if not text:
            self.status_text = "Nothing to paste"
            self._refresh_ui()
            return
        try:
            copy_to_clipboard(text)
            pasted = autopaste_text()
        except Exception as exc:  # noqa: BLE001
            self.status_text = "Paste failed"
            self.error_text = str(exc)
            self._refresh_ui()
            return
        self.status_text = "Pasted" if pasted else "Copied to clipboard; auto-paste unavailable"
        self.error_text = ""
        self._refresh_ui()

    def _on_clear_clicked(self, _button: Gtk.Button) -> None:
        assert self.text_buffer is not None
        self.text_buffer.set_text("")
        self.status_text = "Cleared transcript"
        self.error_text = ""
        self._refresh_ui()

    def _on_copy_error_clicked(self, _button: Gtk.Button) -> None:
        if not self.error_text:
            self.status_text = "No error to copy"
            self._refresh_ui()
            return
        try:
            copy_to_clipboard(self.error_text)
        except Exception as exc:  # noqa: BLE001
            self.status_text = "Copy failed"
            self.error_text = str(exc)
            self._refresh_ui()
            return
        self.status_text = "Copied error to clipboard"
        self._refresh_ui()

    def _start_recording(self) -> None:
        self.error_text = ""
        self.status_text = "Recording…"
        self.current_audio_path = self.state_dir / f"capture-{int(time.time() * 1000)}.wav"
        self.pending_language = None
        if self.language_entry is not None:
            text = self.language_entry.get_text().strip()
            self.pending_language = text or None
        self.pending_autopaste = self.autopaste_check.get_active() if self.autopaste_check is not None else True
        self.stop_event = Event()
        self.worker = Thread(target=self._recording_worker, daemon=True)
        self.worker.start()
        self._refresh_ui()

    def _toggle_recording(self) -> None:
        if self.worker and self.worker.is_alive():
            if self.stop_event is not None:
                self.status_text = "Stopping recording…"
                self.stop_event.set()
                self._refresh_ui()
            return
        self._start_recording()

    def _configure_hotkey_status(self) -> None:
        self.hotkey_status_text = "Hotkey Ctrl+]: window-only (focus required)"

    def _handle_window_keypress(
        self,
        _controller: Gtk.EventControllerKey | None,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if keyval != Gdk.KEY_bracketright:
            return False
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        self.hotkey_trigger_count += 1
        self.hotkey_status_text = f"Hotkey Ctrl+]: detected {self.hotkey_trigger_count} time(s)"
        self._toggle_recording()
        self._refresh_ui()
        return True

    def _recording_worker(self) -> None:
        assert self.stop_event is not None
        assert self.current_audio_path is not None
        try:
            result = run_dictation_session(
                state_dir=self.state_dir,
                audio_path=self.current_audio_path,
                stop_event=self.stop_event,
                language=self.pending_language,
                autopaste=self.pending_autopaste,
            )
            self.events.put(("success", result, None, None))
        except Exception as exc:  # noqa: BLE001
            error_path = persist_error(self.state_dir, str(exc), self.current_audio_path)
            self.events.put(("error", str(exc), error_path, self.current_audio_path))
        finally:
            self.events.put(("finished", None, None, None))

    def _drain_events(self) -> bool:
        while True:
            try:
                kind, first, second, third = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "success" and isinstance(first, DictationResult):
                self._handle_success(first)
            elif kind == "error":
                self.status_text = "Failed"
                self.error_text = f"{first}  Details: {second}  Recording: {third}"
                self._refresh_ui()
            elif kind == "finished":
                self.worker = None
                self.stop_event = None
                self.current_audio_path = None
                self._refresh_ui()
        return True

    def _handle_success(self, result: DictationResult) -> None:
        assert self.text_buffer is not None
        self.text_buffer.set_text(result.text)
        self.status_text = "Transcribed and pasted" if result.pasted else "Transcribed and copied to clipboard"
        self.status_text = f"{self.status_text} via {result.backend}"
        self.error_text = ""
        result.audio_path.unlink(missing_ok=True)
        self._refresh_ui()

    def _load_reports(self) -> None:
        assert self.text_buffer is not None
        transcript_path = resolve_transcript_report_path(self.state_dir)
        if transcript_path.exists():
            self.text_buffer.set_text(transcript_path.read_text(encoding="utf-8"))
        error_path = resolve_error_report_path(self.state_dir)
        if error_path.exists():
            self.error_text = error_path.read_text(encoding="utf-8").strip()

    def _current_text(self) -> str:
        assert self.text_buffer is not None
        start = self.text_buffer.get_start_iter()
        end = self.text_buffer.get_end_iter()
        return self.text_buffer.get_text(start, end, False).strip()

    def _refresh_ui(self) -> None:
        if self.status_label is not None:
            self.status_label.set_label(self.status_text)
        if self.hotkey_label is not None:
            self.hotkey_label.set_label(self.hotkey_status_text)
        if self.error_buffer is not None:
            self.error_buffer.set_text(self.error_text)
        if self.error_scroller is not None:
            self.error_scroller.set_visible(bool(self.error_text))
        if self.toggle_button is not None:
            recording = self.worker is not None and self.worker.is_alive()
            self.toggle_button.set_label("Stop Recording" if recording else "Start Recording")

    def _on_close_request(self, _window: Gtk.ApplicationWindow) -> bool:
        if self.stop_event is not None:
            self.stop_event.set()
        return False


def main() -> None:
    app = EasyDictateApplication()
    app.run()


if __name__ == "__main__":
    main()
