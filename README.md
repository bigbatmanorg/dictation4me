# Dictation4me — Native AI Notes & System-Wide Dictation

**Clean replacement of [`bigbatmanorg/dictation4me`](https://github.com/bigbatmanorg/dictation4me).** Python 3.12+, NativeHTMLKit, local SQLite, separately configurable OpenAI-compatible STT and LLM servers. Designed for **KDE Plasma / Wayland Linux and Windows x64**. No browser, JavaScript, HTTP service, Tk, or Node.js.

> **Status:** implemented source and automated mock/SQLite/HTTP/IPC tests; **not yet certified on a real KDE Wayland or Windows desktop**. See [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md). This is a developer handoff, not an independently qualified 1.0 release.

## What the app does

- Native dark/slate notebook: notes, folders, tags, favorites (pin), archive/trash, Markdown textarea and safe read-only preview, autosaved drafts and immutable revision history.
- Record a voice note, transcribe it using an endpoint you choose, and append the original transcript to the selected note without overwriting concurrent manual editing. Original recordings and failed requests are recoverable.
- Transform any note or quick transcription with eight bundled AI presets or your own instructions. AI returns proposals; choose **Use as current**, **Alternative version**, **New note** or **Discard**. The original transcript is retained.
- Resident global dictation works independently of the notes window. On a configured system-wide shortcut: **press to record → press to stop → press to deliver** after transcription completes. During processing a press is a busy no-op. Delivery defaults to **clipboard only**; paste shortcuts are separately opt-in and never count as proof of insertion.
- Separate STT/AI model names, base URLs, optional secure keyring credentials, test controls, retention settings and an explicit remote-provider approval checkbox.
- Backup/restore and Markdown export, recoverable interrupted WAV spool files, CLI diagnostic commands, single-instance resident service, authenticated local IPC.

## Installation from the provided ZIP

Unpack the ZIP into a working directory or copy its files into a **clone** of the existing GitHub repository (preserve `.git` and Git history). **Do not merge the old 0.1.0 implementation files back into the clean replacement.**

Linux and Windows with [uv](https://docs.astral.sh/uv/):

```sh
uv sync --group dev
uv run dictation4me init --autostart
uv run dictation4me gui
```

Or, for an isolated installed tool:

```sh
uv tool install .
dictation4me init --autostart
dictation4me gui
```

`init` writes defaults (without credentials) and optionally creates standard Start Menu / desktop autostart entries. `--autostart` starts the **resident service**, not the notebook, on login; omit it if undesired. `uv tool install` uses the environment's native host wheel provided by the NativeHTMLKit Python SDK. There is no need to start a separate LLM server unless you choose to use a local provider.

**NativeHTMLKit prerequisites:** the released Linux Python SDK wheel currently targets x64 with **glibc 2.39+**. KDE Plasma/Wayland and Windows x64 are the development targets; macOS is not currently supported by NativeHTMLKit Core. Check current upstream compatibility in [`nativehtmlkit-python`](https://github.com/nativehtmlkit/nativehtmlkit-python) before installing on another distribution.

**Microphone/clipboard prerequisites:** `sounddevice` needs a working microphone and PortAudio on Linux; Windows usually obtains PortAudio with its wheel. KDE Wayland requires `wl-copy` and `wl-paste` for clipboard delivery; X11 requires `xclip`. Optional key simulation uses `ydotool` + an explicitly provisioned `ydotoold`/`/dev/uinput` on Wayland (it is **not** installed or granted permissions by this app), or `xdotool` on X11. On Windows, `RegisterHotKey` and Unicode clipboard use Win32; `SendInput` cannot inject into elevated or secure desktops. Run `dictation4me doctor` to see what's available.

## Configure your STT and AI backends in the GUI

1. Open **Settings → Speech-to-text provider**. Enter an OpenAI-compatible base URL, model alias, and endpoint path (default `/audio/transcriptions`). Set request profile to Minimal, OpenAI standard JSON, or Legacy language hint as required by your endpoint.
2. Open **Settings → AI writing provider**. Enter a separate compatible base URL, model alias, and endpoint path (`/chat/completions` by default).
3. For authentication, choose **No key**, **Environment variable**, or **OS keyring**. To save a new token into the OS keyring, paste it in the masked field and save. Keys never go into `config.toml`.
4. If either endpoint is remote/public, explicitly approve sending audio/text to a remote server using the checkbox. Remote providers can receive and retain the content you send. HTTPS certificate validation remains enabled.
5. Save settings. Use **Test AI connection** or **Test STT with a WAV file** explicitly. These test actions send content to your configured server; no hidden example note is transmitted.

The UI functions without any configured providers; attempting dictation/STT/AI with an unconfigured model produces an actionable error while preserving recordings. Settings are not prefilled with your credentials or model names.

## Daily keyboard workflow

| Action | Expected behavior |
|---|---|
| Global `Ctrl+Alt+D` (default), first press | Start a new quick microphone recording |
| Press again while recording | Finalize audio file; submit to configured STT server |
| Press during STT/AI processing | Ignore; do not begin a new recording or paste partial text |
| Press when `READY` | Copy finished, optionally manually edited text; optionally send paste keystrokes |
| Press after delivery | Start a new recording (never auto-repeat the previous paste) |
| `Ctrl+N` in notebook | Create a new note |
| `Ctrl+S` in notebook | Save a manual revision/checkpoint |
| `Ctrl+K` in notebook | Show notebook and focus search |

The KDE GlobalShortcuts portal may require user approval; on desktops that cannot register it, create a KDE custom application shortcut to `dictation4me toggle`. Press behavior is the same. On Windows, the startup service registers the hotkey via `RegisterHotKey` and reports conflicts.

**Clipboard/paste security:** Automatic insertion is **off** by default. Clipboard delivery to the OS is verifiable; the receiving application’s acceptance is not. Windows paste attempts are blocked when the original foreground window is no longer focused. KDE Wayland does not expose the original focused field; clipboard-only is the safe default. Optional synthetic paste to the **current** field on Wayland is explicitly labeled unsafe and requires the user's separate uinput authorization. The application does not elevate privileges or bypass OS security.

**Working with recordings:** A voice note recording is appended to the linked note if the note has not changed since capture. If it changed, the original transcript remains accessible in that note's Recordings section so you can explicitly append it. Quick dictation is temporary (default text retention 24 hours); **Save as note** makes the result a durable note. Quick audio is deleted after successful STT by default, but the Settings checkbox can retain it.

## CLI reference

```sh
dictation4me gui                 # native notebook
dictation4me quick-review        # notebook opened on Quick dictation
dictation4me service start       # detached resident service
dictation4me service status
dictation4me service stop
dictation4me service restart
dictation4me toggle              # external system shortcut fallback
dictation4me cancel              # stop active recording/job, keep recoverable WAV
dictation4me copy-last
dictation4me paste-last          # EXPLICIT repeat; duplicate paste may occur
dictation4me doctor --json
dictation4me devices
dictation4me recover list
dictation4me recover retry RECORDING_ID
dictation4me recover salvage RECORDING_ID
dictation4me backup --output ./my-backup.zip
dictation4me export --output ./notes-export.zip
dictation4me restore --input ./my-backup.zip --destination ./new-empty-profile
dictation4me test microphone --seconds 3
dictation4me test stt --file ./sample.wav
dictation4me test ai
```

Restore always writes to a **new, nonexistent folder**. To use a restored profile, launch Dictation4me with `DICTATION4ME_HOME` set to that folder. Backups and exports are ZIP files **without encryption**; store them securely. `dictation4me uninstall` removes app launchers and preserves notes; `--purge --confirm-purge` explicitly erases profile data and must never be used on a shared or production profile.

## Architecture

```text
                   ┌──────────────────────────────────┐
                   │ NativeHTMLKit Python UI          │
                   │ Markdown editor, notes, settings │
                   └─────────────────┬────────────────┘
                                     │ authenticated localhost JSON RPC
CLI / shortcut command ──────────────┤
                                     ▼
                   ┌──────────────────────────────────┐
                   │ Python resident service          │
                   │ recorder, state machine, jobs,   │
                   │ local OS shortcut/paste adapters │
                   └───────────┬──────────┬───────────┘
                               │          │
                          SQLite/WAV     configured HTTP(S) endpoints
                                          STT and AI independently
```

`src/dictation4me/engine.py` is the service controller. `storage.py` is the SQLite repository. `audio.py` spools PCM16 WAV. `providers.py` contains bounded OpenAI-compatible HTTP requests. `desktop/` isolates Linux/Windows APIs. `gui.py` uses only public NativeHTMLKit Python SDK operations and local HTML/CSS assets. `ipc.py` is a loopback service, not an HTTP server; token and lock files are private. There are no Core/private protocol imports.

For design rationale, detailed product, UX, data contracts, threat model and roadmap see [`design-spec/`](design-spec/). The executable reference contracts are in [`contracts/`](contracts/); acceptance matrix in [`qualification/acceptance.csv`](qualification/acceptance.csv).

## Local development

```sh
uv sync --group dev
uv run pytest -q
uv run python -m compileall -q src tests
uv build
uv run dictation4me doctor --json
```

If using Python directly from an unpacked source checkout, set `PYTHONPATH=src` or install the project with `pip install -e .`. Tests use isolated `DICTATION4ME_HOME` directories; never run test keystroke injection against a normal working document. The `test paste` CLI command requires `--i-understand` because it sends actual key input. The Windows and KDE Wayland interactive acceptance runs must be done on those desktops, not inferred from SSH/headless/Xvfb.

The package intentionally does not ship an unverified lockfile. The local agent should resolve, test and commit an actual `uv.lock` on the connected development machines.

## Current limitations

- No macOS target, no browser, no rich-text `contenteditable` editor, no HTML audio player, no integrated model server, no live streaming transcript, no cloud sync, no agent/plugin framework.
- System-wide floating overlay is not shipped; status is shown via desktop notifications and the notebook's Quick tab. Existing NativeHTMLKit window-visibility/focus restrictions prevent a guaranteed KDE Wayland overlay. Don't confuse a browser mockup with a working overlay.
- Clipboard-only delivery is the safe cross-platform baseline. Input injection and focus handling require per-desktop qualification and are not guaranteed.
- Actual microphone capture, global shortcuts, NativeHTMLKit rendering, IME/screen-reader behavior and Windows native APIs **remain to be qualified interactively**. See [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md).

**Project license:** MIT. Do not copy your old `0.1.x` configuration onto this clean replacement without reviewing the new schema.
