from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from easydictate.core import (
    build_transcription_request,
    build_record_backend_order,
    choose_clipboard_command,
    choose_paste_command,
    format_missing_recording_error,
    format_process_error,
    has_wayland_runtime,
    read_settings,
    require_api_key,
    resolve_error_report_path,
    resolve_transcript_report_path,
)


DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1
LOGGER = logging.getLogger(__name__)


@dataclass
class DictationResult:
    text: str
    pasted: bool
    audio_path: Path
    backend: str


def install_signal_handlers(stop_event: Event) -> None:
    def _handle_signal(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def run_dictation_session(
    *,
    state_dir: Path,
    audio_path: Path,
    stop_event: Event,
    language: str | None = None,
    prompt: str | None = None,
    autopaste: bool = True,
) -> DictationResult:
    settings = read_settings()
    preferred_backend = settings.get("EASYDICTATE_RECORD_BACKEND") or settings.get("record_backend")
    preferred_source = settings.get("EASYDICTATE_RECORD_SOURCE") or settings.get("record_source")
    backend = record_microphone(
        audio_path,
        stop_event,
        preferred_backend=preferred_backend,
        preferred_source=str(preferred_source) if preferred_source else None,
    )
    ensure_recording_exists(audio_path, backend)
    text = transcribe_audio(
        audio_path=audio_path,
        api_key=require_api_key(settings),
        language=language or settings.get("language"),
        prompt=prompt or settings.get("prompt"),
    )
    copy_to_clipboard(text)
    pasted = False
    if autopaste:
        try:
            pasted = autopaste_text()
        except Exception:  # noqa: BLE001
            pasted = False
    persist_transcript(state_dir, text)
    clear_error_report(state_dir)
    return DictationResult(text=text, pasted=pasted, audio_path=audio_path, backend=backend)


def record_microphone(
    audio_path: Path,
    stop_event: Event,
    preferred_backend: str | None = None,
    preferred_source: str | None = None,
) -> str:
    errors: list[str] = []
    for backend in build_record_backend_order(shutil_which, preferred=preferred_backend):
        try:
            LOGGER.info(
                "Attempting recording via %s%s",
                backend,
                f" using source {preferred_source}" if preferred_source else "",
            )
            if backend == "ffmpeg":
                record_with_ffmpeg(audio_path, stop_event, source=preferred_source)
            elif backend == "parecord":
                record_with_parecord(audio_path, stop_event, source=preferred_source)
            elif backend == "pw-record":
                record_with_pw_record(audio_path, stop_event, source=preferred_source)
            elif backend == "arecord":
                record_with_arecord(audio_path, stop_event)
            else:
                record_with_sounddevice(audio_path, stop_event)
            ensure_recording_exists(audio_path, backend)
            return backend
        except Exception as exc:  # noqa: BLE001
            audio_path.unlink(missing_ok=True)
            errors.append(str(exc))
    raise RuntimeError(" | ".join(errors))


def ensure_recording_exists(audio_path: Path, backend: str) -> None:
    if not audio_path.exists():
        raise RuntimeError(format_missing_recording_error(backend, audio_path))
    if audio_path.stat().st_size == 0:
        raise RuntimeError(f"{backend} produced an empty recording file at {audio_path}")


def record_with_sounddevice(audio_path: Path, stop_event: Event) -> None:
    try:
        import sounddevice as sd
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "No recorder backend found. Install PipeWire `pw-record`, ALSA `arecord`, or the Python sounddevice package."
        ) from exc

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    chunks: queue.Queue[bytes] = queue.Queue()
    callback_error: list[str] = []

    def callback(indata: bytes, _frames: int, _time: Any, status: Any) -> None:
        if status:
            callback_error.append(str(status))
            stop_event.set()
            return
        chunks.put(bytes(indata))

    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(DEFAULT_CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(DEFAULT_SAMPLE_RATE)
        with sd.RawInputStream(
            samplerate=DEFAULT_SAMPLE_RATE,
            blocksize=0,
            channels=DEFAULT_CHANNELS,
            dtype="int16",
            callback=callback,
        ):
            while not stop_event.is_set():
                try:
                    wav_file.writeframes(chunks.get(timeout=0.2))
                except queue.Empty:
                    pass
            flush_chunks(chunks, wav_file)
    if callback_error:
        raise RuntimeError(f"Audio input error: {callback_error[0]}")


def record_with_pw_record(audio_path: Path, stop_event: Event, source: str | None = None) -> None:
    command = [
        "pw-record",
        "--format",
        "s16",
        "--rate",
        str(DEFAULT_SAMPLE_RATE),
        "--channels",
        str(DEFAULT_CHANNELS),
        "--container",
        "wav",
    ]
    if source:
        command.extend(["--target", source])
    command.append(str(audio_path))
    run_command_recorder(command, audio_path, "pw-record", stop_event)


def record_with_parecord(audio_path: Path, stop_event: Event, source: str | None = None) -> None:
    command = [
        "parecord",
        "--rate=16000",
        "--channels=1",
        "--format=s16le",
        "--file-format=wav",
    ]
    if source:
        command.append(f"--device={source}")
    command.append(str(audio_path))
    run_command_recorder(command, audio_path, "parecord", stop_event)


def record_with_arecord(audio_path: Path, stop_event: Event) -> None:
    run_command_recorder(
        [
            "arecord",
            "-q",
            "-f",
            "S16_LE",
            "-c",
            str(DEFAULT_CHANNELS),
            "-r",
            str(DEFAULT_SAMPLE_RATE),
            "-t",
            "wav",
            str(audio_path),
        ],
        audio_path,
        "arecord",
        stop_event,
    )


def record_with_ffmpeg(audio_path: Path, stop_event: Event, source: str | None = None) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "pulse",
            "-i",
            source or "default",
            "-ac",
            str(DEFAULT_CHANNELS),
            "-ar",
            str(DEFAULT_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(audio_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        while not stop_event.is_set():
            if process.poll() is not None:
                stderr = process.stderr.read().strip() if process.stderr else ""
                raise RuntimeError(format_process_error("ffmpeg", int(process.returncode or 1), stderr))
            time.sleep(0.1)
        if process.stdin is not None:
            process.stdin.write("q\n")
            process.stdin.flush()
            process.stdin.close()
        return_code = process.wait(timeout=5)
        if return_code != 0:
            stderr = process.stderr.read().strip() if process.stderr else ""
            raise RuntimeError(format_process_error("ffmpeg", return_code, stderr))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def run_command_recorder(command: list[str], audio_path: Path, name: str, stop_event: Event) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        while not stop_event.is_set():
            if process.poll() is not None:
                stderr = process.stderr.read().strip() if process.stderr else ""
                raise RuntimeError(format_process_error(name, int(process.returncode or 1), stderr))
            time.sleep(0.1)
        process.send_signal(signal.SIGINT)
        return_code = process.wait(timeout=5)
        if return_code not in (0, -signal.SIGINT, 130):
            stderr = process.stderr.read().strip() if process.stderr else ""
            raise RuntimeError(format_process_error(name, return_code, stderr))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def flush_chunks(chunks: queue.Queue[bytes], wav_file: wave.Wave_write) -> None:
    while True:
        try:
            wav_file.writeframes(chunks.get_nowait())
        except queue.Empty:
            return


def transcribe_audio(audio_path: Path, api_key: str, language: str | None, prompt: str | None) -> str:
    request_data = build_transcription_request(
        api_key=api_key,
        audio_path=audio_path,
        language=language,
        prompt=prompt,
    )
    command = build_curl_transcription_command(request_data, audio_path)
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"curl exited with code {result.returncode}"
        raise RuntimeError(f"Groq transcription request failed: {detail}")
    payload = parse_transcription_payload(result.stdout)
    text = str(payload.get("text", "")).strip()
    if not text:
        raise RuntimeError("Groq returned an empty transcription")
    return text


def build_curl_transcription_command(request_data: dict[str, Any], audio_path: Path) -> list[str]:
    command = [
        "curl",
        "--silent",
        "--show-error",
        request_data["url"],
        "-H",
        f'Authorization: {request_data["headers"]["Authorization"]}',
        "-H",
        f'Accept: {request_data["headers"]["Accept"]}',
        "-H",
        f'User-Agent: {request_data["headers"]["User-Agent"]}',
    ]
    for key, value in request_data["data"].items():
        command.extend(["-F", f"{key}={value}"])
    command.extend(["-F", f"file=@{audio_path};type=audio/wav"])
    return command


def parse_transcription_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except ValueError:
        raise RuntimeError(f"Groq transcription failed: {stdout.strip() or 'non-JSON response'}") from None
    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            code = error_payload.get("code")
            if message and code:
                raise RuntimeError(f"Groq transcription failed: {message} (code: {code})")
            if message:
                raise RuntimeError(f"Groq transcription failed: {message}")
        message = payload.get("message")
        if message:
            raise RuntimeError(f"Groq transcription failed: {message}")
        return payload
    raise RuntimeError(f"Groq transcription failed: {stdout.strip() or 'unexpected response'}")


def copy_to_clipboard(text: str) -> None:
    command = choose_clipboard_command()
    if command is None:
        raise RuntimeError("No clipboard tool found. Install wl-clipboard, xclip, or xsel.")
    subprocess.run(command, input=text, text=True, check=True)


def autopaste_text() -> bool:
    command = choose_paste_command()
    if command is None:
        if has_wayland_runtime(dict(os.environ)):
            LOGGER.info(
                "Auto-paste unavailable: no Wayland paste helper found. Install `wtype`, "
                "or configure a working `ydotoold` with uinput access."
            )
        else:
            LOGGER.info("Auto-paste unavailable: no supported paste helper found for this session")
        return False
    time.sleep(0.08)
    retries = 4 if command and command[0] == "ydotool" else 1
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(retries):
        try:
            subprocess.run(command, check=True)
            return True
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").lower() if isinstance(exc.stderr, str) else ""
            if command[0] != "ydotool" or "connection refused" not in stderr:
                raise
            last_error = exc
            if attempt < retries - 1:
                time.sleep(0.2)
                continue
            raise last_error
    return False


def persist_error(state_dir: Path, message: str, audio_path: Path | None = None) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    error_path = resolve_error_report_path(state_dir)
    details = message.strip()
    if audio_path is not None:
        details = f"{details}\nRecording path: {audio_path}"
    error_path.write_text(details + "\n", encoding="utf-8")
    return error_path


def clear_error_report(state_dir: Path) -> None:
    resolve_error_report_path(state_dir).unlink(missing_ok=True)


def persist_transcript(state_dir: Path, text: str) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = resolve_transcript_report_path(state_dir)
    transcript_path.write_text(text, encoding="utf-8")
    return transcript_path


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


def clear_state(path: Path) -> None:
    path.unlink(missing_ok=True)


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)
