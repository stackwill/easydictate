from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable


WhichFn = Callable[[str], str | None]


def has_wayland_runtime(env: dict[str, str]) -> bool:
    if env.get("WAYLAND_DISPLAY"):
        return True
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return False
    return any(Path(runtime_dir).glob("wayland-*"))


def has_ydotool_socket(env: dict[str, str]) -> bool:
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return False
    return (Path(runtime_dir) / ".ydotool_socket").exists()


def resolve_state_dir(env: dict[str, str] | None = None) -> Path:
    env = env or dict(os.environ)
    base = env.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "easydictate"
    home = Path(env.get("HOME", Path.home().as_posix()))
    return home / ".local" / "state" / "easydictate"


def resolve_config_file(env: dict[str, str] | None = None) -> Path:
    env = env or dict(os.environ)
    base = env.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "easydictate" / "config.json"
    home = Path(env.get("HOME", Path.home().as_posix()))
    return home / ".config" / "easydictate" / "config.json"


def resolve_error_report_path(state_dir: Path) -> Path:
    return state_dir / "last_error.txt"


def resolve_daemon_log_path(state_dir: Path) -> Path:
    return state_dir / "daemon.log"


def resolve_transcript_report_path(state_dir: Path) -> Path:
    return state_dir / "last_transcript.txt"


def resolve_project_dotenv() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_json_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_settings(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env or dict(os.environ)
    merged: dict[str, Any] = {}
    merged.update(load_dotenv(resolve_project_dotenv()))
    merged.update(load_json_config(resolve_config_file(env)))
    merged.update({key: value for key, value in env.items() if key.startswith("EASYDICTATE_") or key == "GROQ_API_KEY"})
    return merged


def require_api_key(settings: dict[str, Any]) -> str:
    api_key = settings.get("GROQ_API_KEY") or settings.get("api_key")
    if not api_key:
        raise RuntimeError(
            "Missing Groq API key. Set GROQ_API_KEY, add it to .env, or create ~/.config/easydictate/config.json."
        )
    return str(api_key)


def resolve_hotkey(settings: dict[str, Any]) -> str:
    hotkey = settings.get("hotkey")
    if hotkey is None:
        return "CTRL+bracketright"
    text = str(hotkey).strip()
    return text or "CTRL+bracketright"


def resolve_hotkey_mode(settings: dict[str, Any]) -> str:
    mode = str(settings.get("hotkey_mode", "toggle")).strip().lower()
    if mode in {"toggle", "hold"}:
        return mode
    raise RuntimeError("Invalid hotkey_mode. Expected 'toggle' or 'hold'.")


def choose_paste_command(env: dict[str, str] | None = None, which: WhichFn | None = None) -> list[str] | None:
    env = env or dict(os.environ)
    which = which or shutil.which

    if env.get("DISPLAY") and which("xdotool"):
        return ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"]
    wayland_runtime = has_wayland_runtime(env)
    desktop = (env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or "").lower()
    if wayland_runtime and which("wtype"):
        if "gnome" in desktop and which("ydotool") and has_ydotool_socket(env):
            return ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]
        return ["wtype", "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"]
    if wayland_runtime and which("ydotool") and has_ydotool_socket(env):
        return ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]
    return None


def choose_clipboard_command(env: dict[str, str] | None = None, which: WhichFn | None = None) -> list[str] | None:
    env = env or dict(os.environ)
    which = which or shutil.which

    if has_wayland_runtime(env) and which("wl-copy"):
        return ["wl-copy"]
    if env.get("DISPLAY") and which("xclip"):
        return ["xclip", "-selection", "clipboard"]
    if env.get("DISPLAY") and which("xsel"):
        return ["xsel", "--clipboard", "--input"]
    return None


def build_record_backend_order(which: WhichFn | None = None, preferred: str | None = None) -> list[str]:
    which = which or shutil.which
    available: list[str] = []
    if which("ffmpeg"):
        available.append("ffmpeg")
    if which("parecord"):
        available.append("parecord")
    if which("pw-record"):
        available.append("pw-record")
    if which("arecord"):
        available.append("arecord")
    available.append("sounddevice")
    if preferred:
        normalized = preferred.strip().lower()
        if normalized == "sounddevice":
            return ["sounddevice"] + [name for name in available if name != "sounddevice"]
        if normalized in {"ffmpeg", "parecord", "pw-record", "arecord"} and which(normalized):
            return [normalized] + [name for name in available if name != normalized]
    return available


def choose_record_backend(which: WhichFn | None = None, preferred: str | None = None) -> str:
    return build_record_backend_order(which, preferred=preferred)[0]


def format_process_error(name: str, return_code: int, stderr: str) -> str:
    cleaned = stderr.strip()
    if cleaned:
        return f"{name} failed: {cleaned}"
    return f"{name} exited with code {return_code}"


def format_missing_recording_error(backend: str, audio_path: Path) -> str:
    return f"{backend} did not produce a recording file at {audio_path}"


def build_transcription_request(
    *,
    api_key: str,
    audio_path: Path,
    language: str | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"model": "whisper-large-v3-turbo"}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    return {
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "headers": {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "EasyDictate/0.1",
        },
        "data": data,
        "files": {"file": (audio_path.name, None, "audio/wav")},
    }
