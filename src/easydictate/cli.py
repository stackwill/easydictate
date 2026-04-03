from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from threading import Event

from easydictate.core import resolve_state_dir
from easydictate.engine import (
    clear_state,
    install_signal_handlers,
    load_state,
    persist_error,
    pid_is_running,
    run_dictation_session,
    save_state,
    shutil_which,
)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="easydictate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    toggle = subparsers.add_parser("toggle", help="Start or stop dictation")
    toggle.add_argument("--language", help="Optional transcription language code")
    toggle.add_argument("--prompt", help="Optional prompt to bias transcription")
    toggle.add_argument("--no-autopaste", action="store_true", help="Copy to clipboard without trying to paste")
    toggle.set_defaults(func=toggle_recording)

    record = subparsers.add_parser("record", help=argparse.SUPPRESS)
    record.add_argument("--state-dir", required=True)
    record.add_argument("--output", required=True)
    record.add_argument("--language")
    record.add_argument("--prompt")
    record.add_argument("--no-autopaste", action="store_true")
    record.set_defaults(func=record_and_transcribe)

    gui = subparsers.add_parser("gui", help="Open the desktop UI")
    gui.set_defaults(func=open_gui)

    daemon = subparsers.add_parser("daemon", help="Run the background global-shortcut daemon")
    daemon.set_defaults(func=open_daemon)
    return parser


def toggle_recording(args: argparse.Namespace) -> None:
    state_dir = resolve_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "active.json"
    state = load_state(state_file)

    if state and pid_is_running(state["pid"]):
        import os
        import signal

        os.kill(int(state["pid"]), signal.SIGINT)
        notify("EasyDictate", "Stopping recording…")
        return

    if state:
        clear_state(state_file)

    audio_path = state_dir / f"capture-{int(time.time() * 1000)}.wav"
    command = [
        sys.executable,
        "-m",
        "easydictate.cli",
        "record",
        "--state-dir",
        str(state_dir),
        "--output",
        str(audio_path),
    ]
    if args.language:
        command.extend(["--language", args.language])
    if args.prompt:
        command.extend(["--prompt", args.prompt])
    if args.no_autopaste:
        command.append("--no-autopaste")

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    save_state(state_file, {"pid": process.pid, "audio_path": str(audio_path)})
    notify("EasyDictate", "Recording started")


def record_and_transcribe(args: argparse.Namespace) -> None:
    state_dir = Path(args.state_dir)
    state_file = state_dir / "active.json"
    audio_path = Path(args.output)
    stop_event = Event()
    install_signal_handlers(stop_event)

    try:
        result = run_dictation_session(
            state_dir=state_dir,
            audio_path=audio_path,
            stop_event=stop_event,
            language=args.language,
            prompt=args.prompt,
            autopaste=not args.no_autopaste,
        )
        audio_path.unlink(missing_ok=True)
        summary = "Transcription pasted" if result.pasted else "Transcription copied to clipboard"
        notify("EasyDictate", summary)
    except Exception as exc:  # noqa: BLE001
        error_path = persist_error(state_dir, str(exc), audio_path)
        notify("EasyDictate", f"Dictation failed. Details: {error_path}")
        raise
    finally:
        clear_state(state_file)


def open_gui(_args: argparse.Namespace) -> None:
    gui_python = resolve_gui_python(sys.executable)
    if gui_python is None:
        raise RuntimeError("No Python interpreter with PyGObject (`gi`) was found for the GTK GUI.")
    if gui_python == sys.executable:
        from easydictate.gui import main as gui_main

        gui_main()
        return

    src_dir = Path(__file__).resolve().parents[1]
    env = build_gui_env(os.environ, src_dir)
    subprocess.run([gui_python, "-m", "easydictate.gui"], check=True, env=env)


def open_daemon(_args: argparse.Namespace) -> None:
    daemon_python = resolve_gui_python(sys.executable)
    if daemon_python is None:
        raise RuntimeError("No Python interpreter with PyGObject (`gi`) was found for the daemon.")
    if daemon_python == sys.executable:
        from easydictate.daemon import main as daemon_main

        daemon_main()
        return

    src_dir = Path(__file__).resolve().parents[1]
    env = build_gui_env(os.environ, src_dir)
    subprocess.run([daemon_python, "-m", "easydictate.daemon"], check=True, env=env)


def notify(title: str, body: str) -> None:
    if not shutil_which("notify-send"):
        return
    subprocess.run(["notify-send", title, body], check=False)


def python_can_import(executable: str, module: str) -> bool:
    result = subprocess.run(
        [executable, "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def resolve_gui_python(current_python: str) -> str | None:
    candidates = [current_python, "python3", "/usr/bin/python3"]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if python_can_import(candidate, "gi"):
            return candidate
    return None


def build_gui_env(base_env: dict[str, str] | os._Environ[str], src_dir: Path) -> dict[str, str]:
    env = dict(base_env)
    existing = env.get("PYTHONPATH", "")
    paths = [str(src_dir), *resolve_gui_python_paths()]
    if existing:
        paths.extend(part for part in existing.split(os.pathsep) if part)
    deduped_paths: list[str] = []
    for path in paths:
        if path and path not in deduped_paths:
            deduped_paths.append(path)
    env["PYTHONPATH"] = os.pathsep.join(deduped_paths)
    return env


def resolve_gui_python_paths() -> list[str]:
    paths: list[str] = []
    for key in ("purelib", "platlib"):
        path = sysconfig.get_path(key)
        if path and path not in paths:
            paths.append(path)
    return paths


if __name__ == "__main__":
    main()
