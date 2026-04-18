# Troubleshooting

## No shortcut prompt appears

Check the service first:

```bash
systemctl --user status easydictate.service
journalctl --user -u easydictate.service -n 100 --no-pager
```

EasyDictate needs:

- A running GNOME session
- `org.freedesktop.portal.GlobalShortcuts` on the session bus
- A matching desktop entry for the registered app ID

The daemon log should contain lines like:

- `Registered app id com.easydictate.app with host portal registry`
- `Bound portal shortcut ...`

## Shortcut registers but nothing happens

Check:

- `~/.config/easydictate/config.json`
- `hotkey_mode`
- daemon log output while triggering the shortcut

For `hold` mode, recording starts on press and stops on release.

## Transcript is copied but not pasted

This usually means clipboard worked but synthetic input did not.

Required tools:

- Wayland clipboard: `wl-copy`
- X11 clipboard: `xclip` or `xsel`
- Wayland paste: `wtype` preferred, or `ydotool` with a working `ydotoold`
- X11 paste: `xdotool`

If `ydotool` is installed but auto-paste still does nothing, check the user service:

```bash
systemctl --user status ydotool.service
journalctl --user -u ydotool.service -n 50 --no-pager
```

If the log mentions `failed to open uinput device: Permission denied`, install `wtype` instead.

## Recording fails

Try forcing a recorder backend in `config.json`:

```json
{
  "record_backend": "ffmpeg"
}
```

Or choose one of:

- `parecord`
- `pw-record`
- `arecord`
- `sounddevice`

Inspect:

- `~/.local/state/easydictate/last_error.txt`
- `~/.local/state/easydictate/daemon.log`

## Service does not survive login

Re-run:

```bash
./install.sh
```

Then verify:

```bash
systemctl --user is-enabled easydictate.service
systemctl --user status easydictate.service
```
