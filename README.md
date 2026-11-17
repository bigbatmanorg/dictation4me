# Dictation4me

**System-wide voice dictation and AI-assisted notes for KDE Plasma (Wayland) and Windows.** Press a global shortcut in any app, speak, press again, and the transcript is ready to paste. A native notebook keeps voice notes, lets you rewrite text with your own AI instructions, and never changes your text without asking.

You choose the speech-to-text and AI servers (any OpenAI-compatible endpoint, local or remote). Notes stay in a local SQLite database. No browser, no JavaScript, no cloud account.

![Notes](docs/screenshots/notes.png)

| Quick dictation | Settings |
|---|---|
| ![Recording](docs/screenshots/quick-recording.png) | ![Connections](docs/screenshots/settings-connections.png) |

## Features

- **Global shortcut workflow:** press to record, press to stop and transcribe, press to copy the result. While processing, presses are ignored. Delivery is clipboard-only by default; sending paste keys is opt-in.
- **Always-visible status:** the sidebar shows what dictation is doing (Ready, Recording, Transcribing, Text ready, Needs attention) on every screen, alongside desktop notifications.
- **Notebook:** folders, tags, pins, archive and trash, Markdown with preview, autosave, and a full version history (restoring creates a new version).
- **Voice notes:** record straight into a note. If you kept typing meanwhile, your typing wins and the transcript waits under *Recordings* to be added.
- **AI rewrite:** eight built-in instructions plus your own presets. Results are proposals: *Replace note*, *Keep as version*, *New note* or *Discard*.
- **Nothing is lost:** failed or silent recordings stay in **Recovery** until you retry or discard them.
- **Private by design:** keys come from an environment variable or the OS keyring (never the config file); remote servers need explicit approval; TLS checks cannot be disabled.

## Install

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and on Linux glibc 2.39+ (NativeHTMLKit).

**Linux system packages** (Debian/Ubuntu names):

```sh
sudo apt install libportaudio2 pipewire-alsa wl-clipboard
```

`libportaudio2` is required for the microphone. `pipewire-alsa` lets PortAudio record through PipeWire; without it, raw ALSA devices often refuse 16 kHz. `wl-clipboard` is needed for clipboard delivery on Wayland. On X11, install `xclip` instead.

**Install and register the app:**

```sh
uv tool install .            # or: uv sync && uv run dictation4me …
dictation4me init --autostart
dictation4me gui
```

`init` writes default settings and installs the desktop entry. The desktop entry is **required for the KDE global shortcut**: the shortcut portal only accepts registered apps. `--autostart` starts the background service at login (not the window).

## First run

1. Open **Settings › Connections** and enter the server URL and model for *Speech-to-text* and *AI writing*. Home-network names like `http://pgx:4000/v1` are fine over plain HTTP; internet servers need HTTPS and the "Allow sending… to remote servers" checkbox.
2. Choose how the API key is provided:
   - **System keyring** (recommended): paste the key into "Paste a new key" and save.
   - **Environment variable:** works when the service inherits it, e.g. when started from a terminal. A service started at login does **not** see variables from `~/.bashrc`. The card shows whether the key is available, and **Move key to keyring** fixes it in one click.
3. Use **Test connection** / **Test with a WAV file…** to check each server.
4. Approve the shortcut in the KDE dialog that appears the first time (default `Ctrl+Alt+D`).

### About the global shortcut on KDE

The preferred shortcut in Settings is offered to KDE when the app first registers. After that, **KDE owns the key**. Change it in *System Settings › Keyboard › Shortcuts › Dictation4me*. Settings › Dictation shows the key that is actually active and warns when it differs. Avoid keys KDE already uses (for example `Meta+L` locks the screen). If the portal is unavailable, bind a custom KDE shortcut to `dictation4me toggle`.

## Daily use

| Action | Result |
|---|---|
| Shortcut, first press | Start recording (notification + sidebar turns red) |
| Shortcut, second press | Stop and transcribe |
| Shortcut when *Text ready* | Copy to clipboard; paste with `Ctrl+V` |
| Shortcut after copying | Start a new recording |
| `Ctrl+N` / `Ctrl+S` / `Ctrl+K` in the window | New note / save a version (or save settings) / search |

Pasting into other apps can't be verified. Copying to the clipboard can, so the app reports only what it can confirm. On Wayland, automatic pasting needs a separately configured `ydotoold` and is off by default.

## Command line

```sh
dictation4me gui | quick-review           # open the notebook (or its Quick tab)
dictation4me service start|stop|status|restart
dictation4me toggle | cancel              # same as the shortcut / cancel a capture
dictation4me copy-last | paste-last       # paste-last may duplicate text
dictation4me doctor --json                # dependency and configuration check
dictation4me devices
dictation4me recover list|retry|salvage|delete [ID]
dictation4me test microphone|stt --file x.wav|ai|clipboard
dictation4me backup --output backup.zip
dictation4me export --output notes.zip
dictation4me restore --input backup.zip --destination ./new-profile
dictation4me uninstall [--purge --confirm-purge]
```

`restore` always writes to a new, empty folder; run with `DICTATION4ME_HOME=<folder>` to use it. Backups are unencrypted ZIP files. `uninstall` removes launchers and keeps your notes unless `--purge --confirm-purge` is given.

## Architecture

```text
 NativeHTMLKit notebook (HTML/CSS, Python callbacks) ─┐
 CLI / desktop shortcut command ──────────────────────┤ authenticated loopback JSON-RPC
                                                      ▼
            Resident service (own systemd user unit on Linux)
            recorder · state machine · jobs · shortcut/clipboard adapters
                 │                         │
           SQLite + WAV spool       OpenAI-compatible STT and chat servers
```

The service keeps running when the window closes. Key modules: `engine.py` (controller), `storage.py` (SQLite), `audio.py` (PCM16 WAV spool), `providers.py` (bounded HTTP clients), `desktop/` (KDE portal, X11, Win32 adapters), `gui.py` + `assets/` (notebook). Design specifications are in [`design-spec/`](design-spec/), the UX rationale in [`docs/UX_REVIEW.md`](docs/UX_REVIEW.md), and the qualification evidence in [`qualification/`](qualification/).

## Development

```sh
uv sync --group dev
uv run ruff check src tests
uv run pytest -q
uv build
```

Tests use isolated `DICTATION4ME_HOME` profiles and never register real global shortcuts (`DICTATION4ME_NO_HOTKEY`). Native behaviour (microphone, portal shortcut, focus and paste) must be checked on a real desktop; see [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md).

## Status and limitations

Qualified on KDE Plasma 6 Wayland (Debian 13 and Ubuntu 26.04) with real microphone, portal shortcut, clipboard and OpenAI-compatible servers. **Windows x64 has not been qualified yet.** There is no macOS support, no floating overlay or tray icon (status comes via notifications and the sidebar), no rich-text editor (Markdown source plus preview), and no live streaming transcription.

License: MIT.
