# Dictation4me

A small Python dictation tool for **Linux and Windows**. Record with a global shortcut,
send one WAV file to your LiteLLM/OpenAI-compatible speech endpoint, then copy and
optionally paste the completed text into the focused application.

**Initial implementation, version 0.1.0.** Core behavior and packaging are tested;
real microphone capture, KDE Wayland integration and native Windows desktop behavior
still require acceptance testing on those machines. See [verification status](docs/STATUS.md).
This is an application, not an STT model server. It uses your existing backend.

## Install as a uv tool

Unzip this repository, open a terminal in its root, and install **without `sudo`**.
An ordinary non-editable tool install copies the package into uv's tool environment;
the source checkout is not needed to run it afterward.

### Debian 13 / KDE Plasma (recommended first target)

```bash
uv tool install --python /usr/bin/python3 .
uv tool update-shell
```

Open a new terminal if `dictation4me` is not on `PATH`. Then onboard:

```bash
dictation4me init --install-deps --enable-debian-backports --enable-uinput
```

This prints the proposed system changes and asks permission before using `sudo`.
It installs PortAudio, the ALSA audio plugins, the system Python's Tk support, clipboard utilities and ydotool;
on Debian 13 the latter comes from **official `trixie-backports`**. The explicit
backports switch adds a Debian-signed source if needed. Do not use it on other distros.
The uinput switch grants the active desktop user virtual-keyboard access using
logind/udev. It does **not** grant access to raw keyboard devices, add you to the
broad `input` group, or make `/dev/uinput` world-writable.

It also creates your configuration, application-menu entry and sign-in launcher,
and opens the settings window. Enter your server URL, model alias and API key,
then click **Save & start**. The shortcut portal may ask you to approve a binding.
The default suggestion is **Ctrl+Alt+D**. Press once to record and again to stop.

If you do not want automatic paste or system changes:

```bash
dictation4me init
```

Select clipboard-only mode in settings. Native clipboard utilities and PortAudio
are still necessary; `dictation4me doctor` explains missing prerequisites.

### Other Linux distributions

```bash
uv tool install --python /path/to/python-with-tk .
dictation4me init --install-deps --enable-uinput
```

The package installer includes apt, dnf, pacman and zypper plans. Those distribution
plans are best-effort, not desktop certification. The required Linux components are:
PortAudio, Tcl/Tk for the selected Python, a session D-Bus, `wl-clipboard` on Wayland
or `xclip` on X11; `ydotool` **1.x** with `ydotoold` for Wayland auto-paste, or
`xdotool` on X11. KDE's tray uses D-Bus directly, so no GTK/PyGObject packages are
needed inside the isolated uv environment.

Installing `python3-tk` does not add Tk to a different uv-managed interpreter.
That is why the Debian command selects `/usr/bin/python3` explicitly.

### Windows

Use Python 3.12 or newer with Tcl/Tk (a normal python.org installation includes it),
or a uv-managed Python whose Tk check succeeds:

```powershell
uv tool install --python 3.13 .
uv tool update-shell
dictation4me init
```

Onboarding creates per-user Start Menu entries and a Startup shortcut; no service,
administrator account, registry run key or scheduled task is installed. Save the
speech configuration and click **Save & start**. `sounddevice` supplies PortAudio
on Windows. Windows microphone privacy settings must allow desktop applications.
Use `dictation4me doctor` to check the interpreter and audio devices.

The Windows implementation uses `RegisterHotKey`, the native Unicode clipboard and
`SendInput`. A normal process cannot inject into a higher-integrity application
or a UAC secure desktop. There is no privilege-elevation workaround in this tool.
An SSH-only session is not a substitute for testing in the interactive desktop.

## What onboarding changes

`dictation4me init` is idempotent: rerunning it repairs launchers but preserves
existing settings and credentials. By default it enables startup at sign-in.
Use `--no-autostart` to opt out. `--no-gui` creates integration without opening Tk.
`--dry-run` prints the system plan without changing files.

Linux files:

- `~/.config/dictation4me/config.toml` (mode 0600).
- `~/.local/share/applications/io.github.dictation4me.desktop`.
- `~/.config/autostart/io.github.dictation4me.desktop` when enabled.
- App icons under `~/.local/share/dictation4me/`, operational log under
  `~/.local/state/dictation4me/`, local control token under the user runtime directory.

Windows files:

- `%LOCALAPPDATA%\Dictation4me\config.toml`, data and operational state.
- `Dictation4me.lnk` and `Dictation4me Settings.lnk` in the user's Start Menu.
- `Startup\Dictation4me.lnk` when sign-in launch is enabled.

Launchers bind to the **installed tool's Python interpreter**, not the source tree
or whichever `python` happens to be on your desktop's PATH. After moving the uv
tool installation or changing interpreters, rerun `dictation4me init`.

`DICTATION4ME_HOME` redirects all app state and integration to an isolated directory.
It is mainly a development/testing option, not required for normal use.

## Use it

```bash
dictation4me launch           # start once, without a terminal remaining open
dictation4me settings         # settings and explicit microphone/STT/paste tests
dictation4me toggle           # useful as a desktop custom-shortcut command
dictation4me status           # state and capability report, no transcript or key
dictation4me cancel           # discard recording / cancel pending transcription
dictation4me retry            # retry a failed upload retained in RAM for 5 minutes
dictation4me copy-last        # recover last completed text from app memory
dictation4me paste-last       # explicitly send the paste shortcut again
dictation4me stop             # stop the resident app
```

Settings has a three-second microphone test, a separate **Transcribe test audio**
button (the request is never sent automatically), a Unicode paste test, and diagnostics.

There is no live transcription, model download, LLM cleanup, transcript database,
raw key listener on Wayland, remote control listener, or continuous microphone capture.
The microphone opens only while recording. A max-duration/memory limit stops capture.
The service remains responsive while the request is pending.

### Shortcut and focus behavior

Linux Wayland uses the GlobalShortcuts portal when available. The `.desktop`
identity is registered through the host portal Registry where supported. The desktop
owns the final binding, so changing the requested shortcut may require editing it
in KDE's shortcut settings. A portal permission dialog is part of onboarding,
not something the app can bypass.

If the portal is unavailable or denied, set shortcut backend **external** and bind
`dictation4me toggle` in KDE/GNOME's shortcut settings. X11 uses an X11 passive
key grab. Windows uses `RegisterHotKey`; a conflict is reported in `status`/`doctor`.
No hold-to-talk mode is included in 0.1.0.

**Paste goes to whatever has focus when transcription finishes.** There is no
tracking or restoration of the original text field. Do not switch fields while
waiting unless using clipboard-only mode. The application never sends Enter,
Backspace, Delete or transcript characters as individual keystrokes.

Paste always follows a full Unicode clipboard copy. If another app changes the
clipboard before injection, paste is skipped. A successful key-injection call does
**not** prove the target accepted the text; the UI says “Paste shortcut sent,” not
“Text inserted successfully.” Failed insertion keeps text in the clipboard or
app memory for **Copy last**. Cancellation stops unsent work, but cannot recall
keystrokes already handed to the OS.

For terminals, choose `ctrl+shift+v` or another supported paste shortcut yourself.
There are no automatic application-specific rules. Clipboard persistence after
app exit depends on the platform/clipboard manager. Clipboard histories may
retain the transcript even though Dictation4me does not.

## Configuration

Example (also included as `config.example.toml`):

```toml
[service]
base_url = "http://localhost:4000/v1"
model = "whisper-1"
api_key_env = "LITELLM_API_KEY"
api_key = ""
language = "auto"
timeout_seconds = 120
endpoint_path = ""

[audio]
device = "default"
max_seconds = 300
max_buffer_mb = 128

[output]
auto_paste = true
paste_shortcut = "ctrl+v"
paste_delay_ms = 250
backend = "auto"
ydotool_socket = ""
ydotool_autostart = true

[desktop]
hotkey = "ctrl+alt+d"
hotkey_backend = "auto"
autostart = true
```

A base URL ending in `/v1` is not doubled. A full `/audio/transcriptions` URL is
also accepted. Use `endpoint_path` for a non-standard gateway path.
The request is multipart `model`, WAV `file`, `response_format=json`, and an optional
language. The result must be a JSON object containing a non-empty string `text`.
No API key is required for endpoints that intentionally allow unauthenticated access.

Environment-variable credentials override the stored key. Terminal environment
variables may not reach desktop autostart processes; configure your login session
or save the key using the masked settings field. The local file is private on
Unix. Windows uses the user's profile permissions. **This version does not use an
OS credential vault or encrypt the configuration file.** Avoid placing configuration
in a shared directory. No keys or transcript text are written to routine logs.

Use HTTPS outside a trusted local network. TLS certificate validation stays enabled;
there is no “ignore TLS errors” switch. HTTP(S)_PROXY variables are intentionally
ignored for speech requests. Redirects and automatic POST retries are disabled.
Retry is an explicit user action and can incur a second provider charge.

## Diagnostics and recovery

```bash
dictation4me doctor
dictation4me devices
dictation4me test-output --delay 3
dictation4me test-stt /path/to/recording.wav
dictation4me run              # foreground diagnostics instead of hidden logs
```

Do not run `run` and `launch` as different users. A lock and authenticated loopback
JSON socket enforce one instance per app home. The listener binds only to
`127.0.0.1`, uses an unpredictable token in a private file, accepts bounded messages,
and exposes fixed actions—not arbitrary shell commands. Local code running under
your own user account is outside that security boundary.

For Wayland, inspect `doctor` for `ydotool`, `/dev/uinput`, and an optional external
daemon socket. A missing/stale socket is not considered ready. The app can start its
own unprivileged `ydotoold` in a private runtime directory. Managed helpers are stopped
on clean shutdown; a crash can leave a helper for the next process to reuse.

If active-seat ACLs are unavailable, an explicit fallback grants only `/dev/uinput`
access through a dedicated group (log out/in afterward):

```bash
dictation4me setup-system --enable-uinput --uinput-group
```

This is a meaningful permission change: any process running as that user can synthesize
keyboard input. Do not grant it to untrusted accounts.

## Upgrade / uninstall

```bash
dictation4me stop
uv tool install --reinstall --python /usr/bin/python3 .
dictation4me init --no-gui
dictation4me launch
```

Use the Windows Python selection instead of `/usr/bin/python3` on Windows.
After publishing the repository yourself, the same package can be installed with
`uv tool install git+https://github.com/YOUR-ACCOUNT/YOUR-REPO.git`.
No PyPI or GitHub publication is performed by this archive.

```bash
dictation4me uninstall         # remove launchers, preserve configuration
# or: dictation4me uninstall --purge
uv tool uninstall dictation4me
```

System packages, dedicated-group membership, backports and udev permissions are
not removed automatically because they may now be used by other software.
To revert **only rules this tool created**, remove
`/etc/udev/rules.d/70-dictation4me-uinput.rules` and
`/etc/modules-load.d/dictation4me-uinput.conf`, reload udev, then log out/in.
Remove `/etc/apt/sources.list.d/dictation4me-backports.sources` only if no other
installed packages depend on it. Removing a source does not downgrade packages.

## Develop / verify

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv build
```

To exercise the TTK window under Linux CI:

```bash
DICTATION4ME_TEST_X11=1 xvfb-run -a uv run pytest -q
```

A Linux/Windows CI workflow is included. It builds packages and tests core behavior;
it does not certify physical microphone capture or actual Wayland/Windows input.
No `uv.lock` is supplied from an unverified network resolution. Run `uv lock` on
your connected machine and commit it for your chosen dependency snapshot.

The implementation intentionally keeps the reliable parts of the original
`bigbatmanorg/kde-dictation` design (external STT, clipboard plus paste shortcut,
non-destructive output) while removing realtime reconciliation and KDE C++ build
dependencies. Source map, evidence and remaining acceptance checks are in
[docs/STATUS.md](docs/STATUS.md).

## Primary platform references

- uv tools: https://docs.astral.sh/uv/concepts/tools/
- sounddevice installation: https://python-sounddevice.readthedocs.io/en/latest/installation.html
- GlobalShortcuts portal: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html
- Host application identity: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.host.portal.Registry.html
- Debian 13 ydotool: https://packages.debian.org/trixie-backports/ydotool
- Windows RegisterHotKey: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey
- Windows SendInput limitations: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput

MIT licensed. Python 3.12+. No Node.js/npm, web view, local model weights or GPU required.
