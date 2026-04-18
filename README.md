<div align="center">

<img src="docs/banner.svg" alt="EasyDictate" />

<br/>

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)](https://kernel.org)
[![GNOME](https://img.shields.io/badge/GNOME-Portals-4A86CF?style=for-the-badge&logo=gnome&logoColor=white)](https://gnome.org)
[![Groq](https://img.shields.io/badge/Powered%20by-Groq%20Whisper-F55036?style=for-the-badge&logo=thunderbird&logoColor=white)](https://groq.com)
[![systemd](https://img.shields.io/badge/systemd--user-service-282C34?style=for-the-badge&logo=linux&logoColor=white)]()

<br/>

*Portable GNOME dictation daemon with a portal-based global shortcut, background `systemd --user` service, clipboard copy, and terminal-friendly auto-paste.*

</div>

---

## What It Does

EasyDictate lets you trigger voice dictation from anywhere in a GNOME session without keeping a window open.

```
Press shortcut  →  Record mic  →  Groq Whisper  →  Clipboard  →  Auto-paste
```

1. Press a global shortcut registered through the GNOME Global Shortcuts portal
2. Record microphone audio
3. Send it to Groq Whisper
4. Copy the transcript to the clipboard
5. Try to auto-paste it with `Ctrl+Shift+V`

The default experience is aimed at terminal-heavy workflows, but it also works in other applications when clipboard and synthetic input tools are available.

---

## Highlights

| Feature | Details |
|---|---|
| Global shortcut | Via GNOME portal — no root or `/dev/input` hacks |
| Shortcut modes | `toggle` (press twice) or `hold` (hold to record) |
| Background service | `systemd --user` — starts on login, zero windows |
| Recording backends | `ffmpeg`, `pw-record`, `parecord`, `arecord`, `sounddevice` with fallback |
| Persistent logging | Daemon log + failure reports written to `~/.local/state/easydictate/` |
| Secret handling | Simple repo-local `.env` |

---

## Quick Start

### 1. Add your API key

```bash
cp .env.example .env
# then edit .env and set:
GROQ_API_KEY=your_key_here
```

### 2. Install and enable the daemon

```bash
./install.sh
```

This will:

1. Create `.venv`
2. Try to install `ffmpeg` and `wtype` when they are not already available
3. Install the package
4. Create a default config file with `record_backend` set to `ffmpeg`
5. Write a desktop entry matching the portal app ID
6. Install and enable `easydictate.service`

### 3. Approve the shortcut

On first successful portal bind, GNOME may prompt you to approve the global shortcut registration.

### 4. Test it

| | Default |
|---|---|
| Shortcut | `Ctrl+]` |
| Mode | `toggle` |

**Toggle mode:** first press starts recording, second press stops, transcribes, copies, and attempts paste.

---

## Requirements

**Hard requirements**

- Linux
- GNOME session with `org.freedesktop.portal.GlobalShortcuts`
- Python 3.11+
- Groq API key

**External tools**

| Purpose | Options |
|---|---|
| Recording | `ffmpeg`, `parecord`, `pw-record`, `arecord`, Python `sounddevice` |
| Clipboard (Wayland) | `wl-copy` |
| Clipboard (X11) | `xclip` or `xsel` |
| Auto-paste (Wayland) | `wtype` preferred, `ydotool` only with a working `ydotoold` |
| Auto-paste (X11) | `xdotool` |

---

## Configuration

User config lives at `~/.config/easydictate/config.json`:

```json
{
  "hotkey": "CTRL+bracketright",
  "hotkey_mode": "toggle",
  "language": "en",
  "prompt": "Format the result as clean coding dictation.",
  "record_backend": "ffmpeg",
  "autopaste": true
}
```

| Key | Description |
|---|---|
| `hotkey` | Trigger key sent to the portal |
| `hotkey_mode` | `toggle` or `hold` |
| `language` | Optional transcription language hint |
| `prompt` | Optional transcription bias |
| `record_backend` | Force a specific recorder |
| `autopaste` | Enable or disable synthetic paste |

Full reference: [CONFIG.md](docs/CONFIG.md)

---

## Operations

```bash
# Install and enable the service
./install.sh

# Run in foreground for debugging
./start.sh

# Service management
systemctl --user status easydictate.service
systemctl --user restart easydictate.service
journalctl --user -u easydictate.service -f
```

More detail: [OPERATIONS.md](docs/OPERATIONS.md)

---

## Logs and State

All runtime state lives under `~/.local/state/easydictate/`:

| File | Contents |
|---|---|
| `daemon.log` | Full daemon log |
| `last_error.txt` | Most recent failure report |
| `last_transcript.txt` | Most recent successful transcript |
| `portal.json` | Portal session state |

If a dictation attempt fails, recorder output is left on disk for inspection.

---

## How the Shortcut Works

EasyDictate does **not** use root-only Linux key hooks.

Instead it:

1. Registers a stable app ID with `org.freedesktop.host.portal.Registry`
2. Uses a matching `.desktop` file
3. Creates a portal session
4. Binds a global shortcut through `org.freedesktop.portal.GlobalShortcuts`

This makes it a much better fit for modern GNOME and Wayland than raw `/dev/input` listeners.

---

## Development

```bash
# Run tests
PYTHONPATH=src python -m unittest discover -s tests -v

# Local debugging
PYTHONPATH=src python -m easydictate --help
PYTHONPATH=src python -m easydictate daemon
```

**Project structure:**

| File | Role |
|---|---|
| `src/easydictate/core.py` | Config, paths, backend selection |
| `src/easydictate/engine.py` | Record, transcribe, clipboard, paste |
| `src/easydictate/daemon.py` | Portal registration, service runtime, shortcut handling |
| `src/easydictate/cli.py` | CLI entrypoints |

---

## Troubleshooting

Common issues and solutions: [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

- No shortcut prompt after install
- Clipboard works but paste does not
- Recording backend fails to start
- Service is enabled but not staying up

---

## Project Status

Functional but early-stage. The daemon path is the primary supported workflow. The old GTK UI remains in the repository as legacy code, but the background service is the intended path going forward.

---

## License

No license file has been added yet. Choose a license before publishing this repository publicly.
