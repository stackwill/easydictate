# Operations Guide

## Install

From the repository root:

```bash
./install.sh
```

This script:

1. Creates `.venv` if needed
2. Installs the package in editable mode
3. Creates `~/.config/easydictate/config.json` if missing
4. Writes `~/.local/share/applications/com.easydictate.app.desktop`
5. Installs and enables `~/.config/systemd/user/easydictate.service`

## Start, Stop, Restart

```bash
systemctl --user start easydictate.service
systemctl --user stop easydictate.service
systemctl --user restart easydictate.service
systemctl --user status easydictate.service
```

## Logs

Structured daemon log:

- `~/.local/state/easydictate/daemon.log`

User service journal:

```bash
journalctl --user -u easydictate.service -f
```

## State Files

- `~/.local/state/easydictate/last_error.txt`
- `~/.local/state/easydictate/last_transcript.txt`
- `~/.local/state/easydictate/portal.json`

## Manual Foreground Run

```bash
./start.sh
```

Useful while debugging portal registration, recorder backends, or clipboard behavior.

## Uninstall

```bash
systemctl --user disable --now easydictate.service
rm -f ~/.config/systemd/user/easydictate.service
rm -f ~/.local/share/applications/com.easydictate.app.desktop
systemctl --user daemon-reload
```

Optional cleanup:

```bash
rm -rf ~/.config/easydictate
rm -rf ~/.local/state/easydictate
```
