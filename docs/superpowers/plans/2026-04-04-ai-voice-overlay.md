# AI Voice Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate AI voice mode that reuses the current recording/transcription pipeline, sends the transcript to OpenRouter through a wrapped prompt, and shows the AI response in an ephemeral copyable overlay without changing the existing EasyDictate dictation behavior.

**Architecture:** Keep the current EasyDictate path intact, extract one shared transcript-only helper from the engine, and build the new AI path as sibling modules on top of that helper. The new mode gets its own daemon, app ID, installer, and tests, while the current clipboard/paste workflow remains unchanged.

**Tech Stack:** Python 3.11, `unittest`, GTK/PyGObject, `curl`, GNOME Global Shortcuts portal, `systemd --user`

---

## File Structure

- `src/easydictate/engine.py`: shared record/transcribe path for both modes
- `src/easydictate/ai_client.py`: OpenRouter request + response parsing
- `src/easydictate/ai_mode.py`: transcript -> prompt -> AI response orchestration
- `src/easydictate/ai_overlay.py`: ephemeral response overlay
- `src/easydictate/ai_daemon.py`: separate shortcut daemon for the AI mode
- `pyproject.toml`: separate `easyvoice` console script
- `install_ai.sh`: separate installer and service setup
- `tests/test_engine.py`: shared-engine regression coverage
- `tests/test_ai_client.py`: prompt and payload coverage
- `tests/test_ai_mode.py`: orchestration coverage
- `tests/test_ai_daemon.py`: shortcut and overlay coverage
- `tests/test_install.py`: installer coverage

### Task 1: Extract the Shared Transcript-Only Path

**Files:**
- Modify: `src/easydictate/engine.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Add failing regression tests for the new helper and the old dictation flow**

```python
# tests/test_engine.py
class RunTranscriptionSessionTests(unittest.TestCase):
    def test_returns_text_and_backend_without_clipboard_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            audio_path = state_dir / "capture.wav"
            audio_path.write_bytes(b"RIFFdemo")

            with mock.patch("easydictate.engine.read_settings", return_value={"GROQ_API_KEY": "secret"}):
                with mock.patch("easydictate.engine.record_microphone", return_value="ffmpeg"):
                    with mock.patch("easydictate.engine.transcribe_audio", return_value="hello transcript"):
                        with mock.patch("easydictate.engine.copy_to_clipboard") as copy_mock:
                            result = engine.run_transcription_session(
                                state_dir=state_dir,
                                audio_path=audio_path,
                                stop_event=mock.Mock(),
                            )

        self.assertEqual(result.text, "hello transcript")
        self.assertEqual(result.backend, "ffmpeg")
        copy_mock.assert_not_called()


class RunDictationSessionRegressionTests(unittest.TestCase):
    def test_still_copies_after_shared_refactor(self) -> None:
        transcript = engine.TranscriptionResult(
            text="hello world",
            audio_path=Path("/tmp/capture.wav"),
            backend="ffmpeg",
        )

        with mock.patch("easydictate.engine.run_transcription_session", return_value=transcript):
            with mock.patch("easydictate.engine.copy_to_clipboard") as copy_mock:
                with mock.patch("easydictate.engine.autopaste_text", return_value=False):
                    result = engine.run_dictation_session(
                        state_dir=Path("/tmp/easydictate"),
                        audio_path=Path("/tmp/capture.wav"),
                        stop_event=mock.Mock(),
                    )

        copy_mock.assert_called_once_with("hello world")
        self.assertFalse(result.pasted)
```

- [ ] **Step 2: Run the engine tests and confirm they fail first**

Run: `PYTHONPATH=src python -m unittest tests.test_engine -v`
Expected: FAIL with missing `run_transcription_session` and `TranscriptionResult`

- [ ] **Step 3: Implement the shared helper**

```python
# src/easydictate/engine.py
@dataclass
class TranscriptionResult:
    text: str
    audio_path: Path
    backend: str


def run_transcription_session(
    *,
    state_dir: Path,
    audio_path: Path,
    stop_event: Event,
    language: str | None = None,
    prompt: str | None = None,
) -> TranscriptionResult:
    settings = read_settings()
    preferred_backend = settings.get("EASYDICTATE_RECORD_BACKEND") or settings.get("record_backend")
    backend = record_microphone(audio_path, stop_event, preferred_backend=preferred_backend)
    ensure_recording_exists(audio_path, backend)
    text = transcribe_audio(
        audio_path=audio_path,
        api_key=require_api_key(settings),
        language=language or settings.get("language"),
        prompt=prompt or settings.get("prompt"),
    )
    persist_transcript(state_dir, text)
    clear_error_report(state_dir)
    return TranscriptionResult(text=text, audio_path=audio_path, backend=backend)
```

```python
# src/easydictate/engine.py
def run_dictation_session(
    *,
    state_dir: Path,
    audio_path: Path,
    stop_event: Event,
    language: str | None = None,
    prompt: str | None = None,
    autopaste: bool = True,
) -> DictationResult:
    transcript = run_transcription_session(
        state_dir=state_dir,
        audio_path=audio_path,
        stop_event=stop_event,
        language=language,
        prompt=prompt,
    )
    copy_to_clipboard(transcript.text)
    pasted = autopaste_text() if autopaste else False
    return DictationResult(
        text=transcript.text,
        pasted=pasted,
        audio_path=transcript.audio_path,
        backend=transcript.backend,
    )
```

- [ ] **Step 4: Verify and commit**

Run: `PYTHONPATH=src python -m unittest tests.test_engine -v`
Expected: PASS

```bash
git add src/easydictate/engine.py tests/test_engine.py
git commit -m "refactor: extract shared transcription session"
```

### Task 2: Add OpenRouter Client and AI Orchestration

**Files:**
- Create: `src/easydictate/ai_client.py`
- Create: `src/easydictate/ai_mode.py`
- Create: `tests/test_ai_client.py`
- Create: `tests/test_ai_mode.py`

- [ ] **Step 1: Add failing tests for prompt wrapping, payload parsing, and AI orchestration**

```python
# tests/test_ai_client.py
class PromptTemplateTests(unittest.TestCase):
    def test_wraps_transcript_into_template(self) -> None:
        wrapped = ai_client.render_prompt("Answer clearly:\n{{transcript}}", "explain decorators")
        self.assertEqual(wrapped, "Answer clearly:\nexplain decorators")


class ParseOpenRouterPayloadTests(unittest.TestCase):
    def test_extracts_message_content(self) -> None:
        payload = '{"choices":[{"message":{"content":"Here is the answer."}}]}'
        self.assertEqual(ai_client.parse_openrouter_payload(payload), "Here is the answer.")
```

```python
# tests/test_ai_mode.py
class RunAiVoiceSessionTests(unittest.TestCase):
    def test_uses_transcription_session_and_never_touches_clipboard(self) -> None:
        transcript = engine.TranscriptionResult(
            text="ask ai about decorators",
            audio_path=Path("/tmp/capture.wav"),
            backend="ffmpeg",
        )
        settings = {
            "OPENROUTER_API_KEY": "secret",
            "ai_model": "openai/gpt-4o-mini",
            "ai_prompt_template": "Answer clearly:\n{{transcript}}",
        }

        with mock.patch("easydictate.ai_mode.read_settings", return_value=settings):
            with mock.patch("easydictate.ai_mode.run_transcription_session", return_value=transcript):
                with mock.patch("easydictate.ai_mode.generate_ai_response", return_value="Decorators wrap callables."):
                    with mock.patch("easydictate.engine.copy_to_clipboard") as copy_mock:
                        result = ai_mode.run_ai_voice_session(
                            state_dir=Path("/tmp/easyvoice"),
                            audio_path=Path("/tmp/capture.wav"),
                            stop_event=mock.Mock(),
                        )

        self.assertEqual(result.response_text, "Decorators wrap callables.")
        copy_mock.assert_not_called()
```

- [ ] **Step 2: Run the new tests and confirm they fail first**

Run: `PYTHONPATH=src python -m unittest tests.test_ai_client tests.test_ai_mode -v`
Expected: FAIL with missing AI modules

- [ ] **Step 3: Implement the AI client and orchestration**

```python
# src/easydictate/ai_client.py
def render_prompt(template: str, transcript: str) -> str:
    normalized = template.strip() or "{{transcript}}"
    if "{{transcript}}" in normalized:
        return normalized.replace("{{transcript}}", transcript.strip())
    return normalized + "\n\n" + transcript.strip()


def parse_openrouter_payload(stdout: str) -> str:
    payload = json.loads(stdout)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenRouter returned no choices")
    content = str(choices[0].get("message", {}).get("content", "")).strip()
    if not content:
        raise RuntimeError("OpenRouter returned an empty response")
    return content
```

```python
# src/easydictate/ai_mode.py
@dataclass
class AiVoiceResult:
    transcript_text: str
    response_text: str
    audio_path: Path
    backend: str


def run_ai_voice_session(*, state_dir: Path, audio_path: Path, stop_event: Event) -> AiVoiceResult:
    settings = read_settings()
    transcript = run_transcription_session(
        state_dir=state_dir,
        audio_path=audio_path,
        stop_event=stop_event,
        language=settings.get("language"),
        prompt=settings.get("prompt"),
    )
    response_text = generate_ai_response(transcript.text, settings)
    return AiVoiceResult(
        transcript_text=transcript.text,
        response_text=response_text,
        audio_path=transcript.audio_path,
        backend=transcript.backend,
    )
```

- [ ] **Step 4: Verify and commit**

Run: `PYTHONPATH=src python -m unittest tests.test_ai_client tests.test_ai_mode -v`
Expected: PASS

```bash
git add src/easydictate/ai_client.py src/easydictate/ai_mode.py tests/test_ai_client.py tests/test_ai_mode.py
git commit -m "feat: add openrouter ai voice flow"
```

### Task 3: Add the Overlay and Separate AI Daemon

**Files:**
- Create: `src/easydictate/ai_overlay.py`
- Create: `src/easydictate/ai_daemon.py`
- Modify: `pyproject.toml`
- Create: `tests/test_ai_daemon.py`

- [ ] **Step 1: Add failing tests for shortcut handling and overlay display**

```python
# tests/test_ai_daemon.py
class AiVoiceDaemonTests(unittest.TestCase):
    def build_daemon(self) -> ai_daemon.AiVoiceDaemon:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return ai_daemon.AiVoiceDaemon(
            config=ai_daemon.AiDaemonConfig(hotkey="CTRL+bracketleft", hotkey_mode="toggle"),
            state_dir=Path(tmp.name),
            logger=mock.Mock(),
        )

    def test_toggle_shortcut_starts_recording(self) -> None:
        daemon = self.build_daemon()
        with mock.patch.object(daemon, "start_recording") as start_mock:
            daemon.handle_shortcut_activated("voice_ai")
        start_mock.assert_called_once_with()

    def test_worker_shows_overlay_on_success(self) -> None:
        daemon = self.build_daemon()
        daemon.stop_event = mock.Mock()
        daemon.audio_path = Path("/tmp/capture.wav")
        result = ai_mode.AiVoiceResult(
            transcript_text="ask ai",
            response_text="Here is the response.",
            audio_path=Path("/tmp/capture.wav"),
            backend="ffmpeg",
        )

        with mock.patch("easydictate.ai_daemon.run_ai_voice_session", return_value=result):
            with mock.patch("easydictate.ai_daemon.show_overlay") as overlay_mock:
                daemon._recording_worker()

        overlay_mock.assert_called_once_with("Here is the response.")
```

- [ ] **Step 2: Run the daemon tests and confirm they fail first**

Run: `PYTHONPATH=src python -m unittest tests.test_ai_daemon -v`
Expected: FAIL with missing `easydictate.ai_daemon`

- [ ] **Step 3: Implement the overlay, daemon, and console script**

```python
# src/easydictate/ai_overlay.py
def show_overlay(text: str) -> None:
    loop = GLib.MainLoop()
    window = Gtk.Window()
    window.set_title("EasyVoice")
    window.set_decorated(False)
    window.set_modal(False)
    window.set_hide_on_close(True)

    buffer = Gtk.TextBuffer()
    buffer.set_text(text)
    view = Gtk.TextView(buffer=buffer)
    view.set_editable(False)
    view.set_cursor_visible(True)
    window.set_child(view)

    def close_overlay(*_args: object) -> bool:
        window.close()
        if loop.is_running():
            loop.quit()
        return False

    key = Gtk.EventControllerKey()
    key.connect("key-pressed", close_overlay)
    window.add_controller(key)
    window.connect("close-request", close_overlay)
    window.present()
    loop.run()
```

```python
# src/easydictate/ai_daemon.py
APP_ID = "com.easydictate.voiceai"
SHORTCUT_ID = "voice_ai"


@dataclass
class AiDaemonConfig:
    hotkey: str
    hotkey_mode: str


class AiVoiceDaemon:
    def handle_shortcut_activated(self, shortcut_id: str) -> None:
        if shortcut_id == SHORTCUT_ID:
            if self.is_recording():
                self.stop_recording()
            else:
                self.start_recording()

    def _recording_worker(self) -> None:
        result = run_ai_voice_session(
            state_dir=self.state_dir,
            audio_path=self.audio_path,
            stop_event=self.stop_event,
        )
        show_overlay(result.response_text)
```

```toml
# pyproject.toml
[project.scripts]
easydictate = "easydictate.cli:main"
easyvoice = "easydictate.ai_daemon:main"
```

- [ ] **Step 4: Verify and commit**

Run: `PYTHONPATH=src python -m unittest tests.test_ai_daemon tests.test_daemon -v`
Expected: PASS

```bash
git add src/easydictate/ai_overlay.py src/easydictate/ai_daemon.py pyproject.toml tests/test_ai_daemon.py
git commit -m "feat: add ai voice daemon and overlay"
```

### Task 4: Add the Separate Installer and Final Regression Coverage

**Files:**
- Create: `install_ai.sh`
- Modify: `README.md`
- Modify: `tests/test_install.py`

- [ ] **Step 1: Add failing installer tests**

```python
# tests/test_install.py
class AiInstallScriptTests(unittest.TestCase):
    def test_ai_install_uses_graphical_session_target(self) -> None:
        content = Path("install_ai.sh").read_text(encoding="utf-8")
        self.assertIn("WantedBy=graphical-session.target", content)

    def test_ai_install_uses_distinct_service_and_desktop_entry(self) -> None:
        content = Path("install_ai.sh").read_text(encoding="utf-8")
        self.assertIn("easyvoice.service", content)
        self.assertIn("com.easydictate.voiceai.desktop", content)
        self.assertIn(".venv/bin/easyvoice", content)
```

- [ ] **Step 2: Run the installer tests and confirm they fail first**

Run: `PYTHONPATH=src python -m unittest tests.test_install -v`
Expected: FAIL because `install_ai.sh` does not exist yet

- [ ] **Step 3: Implement the separate installer and README note**

```bash
# install_ai.sh
SERVICE_PATH="$SERVICE_DIR/easyvoice.service"
DESKTOP_PATH="$APPLICATIONS_DIR/com.easydictate.voiceai.desktop"

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=EasyVoice background daemon
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/easyvoice
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=graphical-session.target
EOF
```

```md
## AI Voice Mode

Run `./install_ai.sh`, then check `systemctl --user status easyvoice.service`.
Default shortcut: `Ctrl+[`
Flow: `record -> transcribe -> ask OpenRouter -> show overlay`
```

- [ ] **Step 4: Verify the installer and run the full suite**

Run: `PYTHONPATH=src python -m unittest tests.test_install -v`
Expected: PASS

Run: `PYTHONPATH=src python -m unittest discover -s tests -v`
Expected: PASS with the old dictation tests and the new AI mode tests green

```bash
git add install_ai.sh README.md tests/test_install.py
git commit -m "feat: add separate ai voice installer"
```

## Self-Review

- Spec coverage is complete: shared transcript path, OpenRouter wrapping, separate daemon, overlay, separate installer, and regression coverage all map to explicit tasks.
- The plan stays within 4 tasks and keeps the new feature isolated instead of refactoring the whole app.
- Naming is consistent across tasks: `TranscriptionResult`, `AiVoiceResult`, `run_transcription_session`, `run_ai_voice_session`, and `show_overlay`.
