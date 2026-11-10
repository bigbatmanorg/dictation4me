# Dictation4me 0.1.0 — implementation and verification

Snapshot: 2026-11-10. This document describes the delivered source, not a claim of
production certification. Nothing has been pushed to the original repository or
published to PyPI. The original C++ project is not overwritten or auto-migrated.

## Verdict

**Implemented and packaged; desktop acceptance remains necessary.** The core,
HTTP transport, controller, control channel, Linux onboarding and TTK window have
executable verification. Native KDE Wayland microphone/clipboard/injection and
Windows desktop behavior are implemented but have not been exercised on those
real desktops in the build environment.

The delivery environment had no external package-download connectivity. Installed
packages were used for testing and setuptools packaging. Missing dependencies
were not replaced with pretend implementations. The source intentionally does
not include a fabricated lockfile: resolve and commit `uv.lock` on a connected
machine. The included CI resolves dependencies and runs Linux/Windows tests, but
those hosted CI jobs have not been run as part of this delivery.

## Evidence

| Check | Result | Scope |
|---|---|---|
| Python test suite | 96 passed, 1 skipped | Python 3.13.5 on Linux; exact command below |
| Batch HTTP request | PASS | Real localhost TCP upload, multipart WAV and Unicode JSON result; plus MockTransport failure cases |
| Audio conversion | PASS | Synthetic PCM, mono/stereo, clipping, 16/44.1/48/96 kHz inputs, valid PCM16 WAV output |
| Recorder callback bounds | PASS | Memory cap and stream error handling with synthetic callbacks; no microphone hardware |
| Controller | PASS | State transitions, busy/debounce handling, retry, cancellation, retained result, no late paste after cancel |
| Resident service / IPC | PASS | Async service and real authenticated loopback socket with injected recorder/output; token denial and instance lock |
| Background process lifecycle | PASS | Real detached process launch, status/control, graceful shutdown and honest missing-capability reporting |
| X11 global shortcut | PASS | Actual passive X11 grab and XTest-generated keys on an isolated Xvfb display |
| TTK settings | PASS | Actual window created under Xvfb; collect/validate/close and visible footer at smaller size; screenshot inspected |
| Linux onboarding | PASS | Real files in isolated app home; idempotence, preservation of credentials, private permissions, launcher quoting and removal |
| Win32 declarations | PASS | INPUT structure sizes and balanced key sequence tests on Linux; not native API execution |
| D-Bus service introspection | SKIPPED | `dbus-next` is declared as a Linux dependency but unavailable in this offline environment |
| Physical microphone / LiteLLM deployment | NOT RUN | No real microphone or access to the user's configured backend |
| KDE Wayland portal, tray and paste | NOT RUN | No KDE Wayland compositor/session in this environment |
| Native Windows UI / clipboard / hotkey | NOT RUN | No interactive Windows desktop available here |
| System package and udev changes | NOT APPLIED | Require explicit authorization on the target machine; test environment was not modified |
| External dependency resolution | NOT RUN | No package-download connectivity; no claim of a full `uv sync` |
| Ruff | NOT RUN | Ruff unavailable offline; byte-compilation and import/name review performed, but not a substitute for Ruff |

Reproduce the locally executed desktop tests **only on a dedicated test display**:

```bash
# A fresh Xvfb server must have matching Xauthority settings.
# Do not point this test at your day-to-day desktop: it synthesizes shortcut keys.
DICTATION4ME_TEST_X11=1 xvfb-run -a uv run pytest -q
uv run python -m compileall -q src tests scripts
```

The normal `uv run pytest -q` skips the opt-in X11 input test. The TTK test also
skips when Linux has no DISPLAY. On Windows, Linux-specific tests are skipped.
Numbers therefore vary appropriately by platform and available capabilities.

### Packaging verification

Packaging smoke results are recorded in `docs/BUILD_EVIDENCE.txt` in the source
archive. An isolated uv installation with external dependencies explicitly excluded verifies the installed
entry point, first-run initialization and removal outside the source tree. It
**does not** establish that all external dependencies can be downloaded or that
microphone/desktop integrations work. On a connected machine, use the normal
installation commands in README, without dependency-exclusion options.

## Implementation map

| Module | Responsibility |
|---|---|
| `cli.py` | Public commands, onboarding, diagnostics and test entry points |
| `app.py` | Resident service, background launch, tray/hotkey adapters, operational logs |
| `controller.py` | One recording/request/output at a time, cancellation, retry and last-result memory |
| `audio.py` | Bounded PortAudio capture, device selection, mono conversion and resampling |
| `stt.py` | OpenAI-compatible batch request, explicit timeout/size limits and sanitized errors |
| `config.py`, `paths.py` | Validated TOML, environment-key precedence, private/atomic per-user files |
| `ipc.py` | Single-instance locking and token-authenticated loopback JSON commands |
| `onboarding.py` | Linux desktop entries, Windows shortcuts and opt-in native prerequisite setup |
| `diagnostics.py` | Read-only actionable capability checks |
| `desktop/linux.py` | wl-clipboard/xclip ownership; ydotool/xdotool paste, socket discovery and helper lifecycle |
| `desktop/linux_bus.py` | Pure-Python StatusNotifierItem/DBusMenu and GlobalShortcuts portal |
| `desktop/x11_hotkey.py` | X11 passive shortcut binding, repeat suppression |
| `desktop/windows.py` | Native Unicode clipboard, hidden owner window, message pump, RegisterHotKey and SendInput |
| `desktop/windows_tray.py` | Windows notification area integration |
| `ui/settings.py` | On-demand TTK settings and explicit audio/STT/paste tests, separate from resident process |

Runtime dependencies are declared in `pyproject.toml`; Linux and Windows extras
are selected with platform markers. No browser, Node/npm, local GPU/model runtime,
GTK/PyGObject or Qt/KDE build chain is required by this implementation.

## Target-machine acceptance checklist

### Debian 13 KDE Wayland

1. Install with the system Python as documented. Run onboarding and review any
   requested system-package/uinput changes. Set your actual speech URL/model/key.
2. Run `dictation4me doctor` and inspect `dictation4me status`. Verify the microphone,
   native clipboard tools, ydotool socket/permission, tray and shortcut status.
3. Approve the portal shortcut. If this desktop rejects/unavailable portal binding,
   choose **external** and assign `dictation4me toggle` in KDE keyboard shortcuts.
4. Run the settings microphone test, then explicitly transcribe that sample.
   Check English and Czech/Slovak Unicode output against your actual backend.
5. Run the paste test in a disposable Kate document, browser text area and VS Code.
   Separately test a terminal with Ctrl+Shift+V. Confirm the app never sends Enter.
6. Record using the global shortcut. Confirm exactly one completed paste, no focus
   stealing and an immediately reusable microphone after finishing/cancelling.
7. Make the STT endpoint unavailable: stop recording, confirm an error, restore
   connectivity and use Retry. Confirm one paste, not duplicates.
8. Cancel while waiting on STT. Confirm no late paste. Copy other text while a
   transcript is finalizing: confirm the changed clipboard is not pasted as the
   transcript. Use Copy last to recover the result.
9. Test unplug/replug/default-device changes, a conflicting shortcut, clipboard-only
   mode, sign-out/sign-in autostart and uninstall/reinstall. Do not infer full
   Wayland support from an Xvfb/X11 test.

### Windows interactive desktop

Install with a Python that has working Tk. Validate actual Start Menu/Startup
shortcuts, microphone privacy permission, tray status, RegisterHotKey, clipboard
Unicode (including non-BMP characters), Notepad/browser/VS Code/terminal targets,
shutdown/restart and cancellation. Check a shortcut conflict. Verify elevated
applications are reported as restricted instead of silently claiming insertion.
Do not run native input acceptance from an SSH-only or locked session.

### Other Linux desktops

Test their specific shortcut portal and native clipboard support. Some desktops
lack a tray host or compatible GlobalShortcuts portal. External command shortcuts
and clipboard-only mode are deliberate fallbacks, not evidence that every
compositor supports automatic paste. Multi-seat behavior is not certified.

## Intentional limitations

- Batch-only transcription; toggle activation. No push-to-talk, live text, LLM cleanup,
  local speech model, audio archive or transcript-history database.
- Target is the focused field **at paste time**, not the field where recording
  began. No accessibility API insertion, focus restoration or application-specific
  keyboard rule detection. Paste acknowledgment is not field-acceptance proof.
- Saved API keys are in a private configuration file, not an OS credential vault.
  The key can instead come from an environment variable visible to the session.
- Most-recent transcript and retry audio live in memory only. Failure audio expires
  after five minutes. Process exit loses those buffers. Clipboard managers may
  keep their own history independently.
- `/dev/uinput` authorization is a significant permission change. No broad input
  group or world-writable device is used. The explicit dedicated-group fallback
  is separate from active-seat ACLs and may need logout/login.
- Output helpers are reaped on clean shutdown; a process crash can leave a reusable
  private ydotoold. There is no system service/privileged daemon installed for the app.
- Proxy environment variables, redirects and automatic POST retries are disabled.
  Custom CA/keyring/provider plugins and compressed long-audio uploads are not included.
- `uv tool install` installs Python dependencies, not OS packages. Onboarding can
  offer supported distro package changes, but cannot bypass administrator permission
  or portal/microphone consent. Clipboard-only mode still requires a native clipboard
  tool on Linux.

Future changes should preserve these truthful boundaries. First run the target
acceptance checklist, repair any integration failures with focused tests, and
only then change this verdict or advertise verified desktop support.
