#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

REPO_DIR="$(pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CONFIG_DIR="$CONFIG_HOME/easydictate"
STATE_DIR="$STATE_HOME/easydictate"
SERVICE_DIR="$CONFIG_HOME/systemd/user"
SERVICE_PATH="$SERVICE_DIR/easydictate.service"
CONFIG_PATH="$CONFIG_DIR/config.json"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_PATH="$APPLICATIONS_DIR/com.easydictate.app.desktop"

mkdir -p "$CONFIG_DIR" "$STATE_DIR" "$SERVICE_DIR" "$APPLICATIONS_DIR"

install_ffmpeg_if_missing() {
  if command -v ffmpeg >/dev/null 2>&1; then
    return
  fi

  printf 'ffmpeg not found; attempting to install it for the default recorder backend.\n'

  if command -v pacman >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo pacman --noconfirm --needed -S ffmpeg || true
  elif command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo apt-get update || true
    sudo apt-get install -y ffmpeg || true
  elif command -v dnf >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo dnf install -y ffmpeg || true
  elif command -v zypper >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo zypper --non-interactive install ffmpeg || true
  else
    printf 'Warning: could not auto-install ffmpeg on this system. Install it manually to use the default backend.\n' >&2
  fi

  if ! command -v ffmpeg >/dev/null 2>&1; then
    printf 'Warning: ffmpeg is still unavailable; EasyDictate will fall back to other recording backends until it is installed.\n' >&2
  fi
}

install_ffmpeg_if_missing

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
python -m pip install -e .

CONFIG_PATH="$CONFIG_PATH" python - <<'PY'
import json
import os
from pathlib import Path

config_path = Path(os.environ["CONFIG_PATH"])
defaults = {
    "hotkey": "CTRL+bracketright",
    "hotkey_mode": "toggle",
    "record_backend": "ffmpeg",
}

if config_path.exists():
    data = json.loads(config_path.read_text(encoding="utf-8"))
else:
    data = {}

for key, value in defaults.items():
    data.setdefault(key, value)

config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=EasyDictate background daemon
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/easydictate daemon
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

cat > "$DESKTOP_PATH" <<EOF
[Desktop Entry]
Type=Application
Name=EasyDictate
Exec=$REPO_DIR/.venv/bin/easydictate daemon
Terminal=false
Categories=Utility;
EOF

systemctl --user daemon-reload
systemctl --user enable --now easydictate.service

printf 'EasyDictate installed.\n'
printf 'Config: %s\n' "$CONFIG_PATH"
printf 'Log: %s\n' "$STATE_DIR/daemon.log"
printf 'Desktop entry: %s\n' "$DESKTOP_PATH"
printf 'Service: systemctl --user status easydictate.service\n'
