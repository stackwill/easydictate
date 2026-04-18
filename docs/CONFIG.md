# Configuration Reference

EasyDictate reads configuration from:

- Repo-local `.env` for secrets such as `GROQ_API_KEY`
- `~/.config/easydictate/config.json` for user settings

## `.env`

```bash
GROQ_API_KEY=your_key_here
```

## `config.json`

Example:

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

## Settings

### `hotkey`

- Type: string
- Default: `CTRL+bracketright`
- Used as the preferred trigger passed to the GNOME Global Shortcuts portal.
- After changing it, restart the user service:

```bash
systemctl --user restart easydictate.service
```

### `hotkey_mode`

- Type: string
- Allowed values: `toggle`, `hold`
- Default: `toggle`

Behavior:

- `toggle`: first shortcut press starts recording, second press stops and transcribes
- `hold`: shortcut press starts recording, shortcut release stops and transcribes

### `language`

- Type: string
- Optional
- Passed through to Groq Whisper as the transcription language hint

### `prompt`

- Type: string
- Optional
- Passed through to Groq Whisper as a transcription prompt bias

### `record_backend`

- Type: string
- Optional
- Default: `ffmpeg` when the config file is created by `./install.sh`
- Valid values: `ffmpeg`, `parecord`, `pw-record`, `arecord`, `sounddevice`

If omitted, EasyDictate tries backends in this order:

1. `ffmpeg`
2. `parecord`
3. `pw-record`
4. `arecord`
5. `sounddevice`

### `autopaste`

- Type: boolean
- Default: `true`

When enabled, EasyDictate copies the transcript to the clipboard and then attempts an automatic paste.

Paste shortcut behavior:

- X11: `Ctrl+Shift+V` via `xdotool`
- Wayland: `Ctrl+Shift+V` via `wtype`, or `ydotool` when `ydotoold` is available with uinput access

If auto-paste is not possible in the current session, EasyDictate still copies the transcript to the clipboard.
