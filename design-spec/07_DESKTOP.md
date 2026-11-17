# Desktop integration and truthful delivery

## Support policy

**Qualified targets:** KDE Plasma Wayland on Linux x64 and Windows x64. Other compositors, X11, macOS and headless desktop sessions are *unqualified*. Each desktop adapter reports `supported`, `available`, `permission_required` and `last_tested` separately. `doctor` must not confuse library presence with actual working paste.

## Linux KDE Wayland

**Hotkeys:** prefer `org.freedesktop.portal.GlobalShortcuts` session registration via D-Bus. Portal prompts user to bind shortcuts and emits activation/deactivation independently of focused window. If unavailable/denied, provide **external shortcut command** for KDE to invoke `dictation4me toggle`; do not use raw X11 grabs on Wayland. Preserve application ID/session identity and detect denied/conflicting binding. Never trap globally pressed keys or read other applications' keyboard events.

**Clipboard:** use current NativeHTMLKit public clipboard API when GUI is available, but resident service must use OS clipboard independently (`wl-copy/wl-paste` or validated native implementation). Require capability check. Clipboard manager may persist text after service quits; warn user for sensitive notes. Clipboard read-back/ownership check before injecting is mandatory; no false success if clipboard was overwritten.

**Paste:** Wayland security prevents ordinary apps from selecting/refocusing arbitrary windows. Optional `ydotool`/uinput can synthesize Ctrl+V only with explicit permission and user-owned private daemon socket; this has a security cost. Default **copy only** until setup/qualification; optional 'paste to currently focused field' mode can be enabled with clear warning. Never advertise guaranteed original-window restoration. Never make `/dev/uinput` globally writable or join broad `input` group without explicit user choice; if permission unavailable keep clipboard mode. For terminal targets use Ctrl+Shift+V when configured/tested.

**Notifications/status:** freedesktop notification service/portal best effort; desktop tray via StatusNotifierItem if supported and useful, separate from NativeHTMLKit Core (Core currently has no native tray API). Avoid blocking recording when notification backend fails. Optional brief sound cue can serve as state indication, but microphone must not start invisibly without an indicator when privacy requires one.

**Launcher/autostart:** freedesktop `.desktop` in per-user paths; XDG autostart with explicit opt-in. No root/systemd service required, no background HTTP listener. KDE shortcut may be configured to invoke a single CLI command rather than portal on environments that lack portal integration.

**Wayland window caveat:** current NativeHTMLKit `Window.set` refuses focus/visibility changes on an existing native Wayland window, and window creation with `visible=False` may fail. Do not depend on a hidden persistent NativeHTMLKit window. A reliable floating review overlay is an **upstream capability proposal** only after a minimal repro and test; main notebook window always available as ordinary app.

## Windows

**Hotkey:** `RegisterHotKey` with `MOD_NOREPEAT`, dedicated background message pump and proper `UnregisterHotKey` at shutdown. Detect conflicts and report them. A tiny message-only native window is allowed inside platform adapter; not part of NativeHTMLKit UI and doesn't become a second framework.

**Clipboard:** use Unicode `CF_UNICODETEXT` and clipboard ownership with bounded retries. Preserve exact diacritics/emoji/newlines. If clipboard locked, surface recoverable error; do not overwrite arbitrary other clipboard formats when no requested action.

**Paste:** `SendInput` can issue configured shortcut only into targets at equal/lower Windows integrity; UIPI may block elevated apps or secure desktop. Capture permitted foreground handle before session start, check immediate foreground identity before injection, skip when mismatched. Injecting keystrokes and verifying input *delivery to target field* are distinct. Report 'shortcut sent' only on API success; protected apps remain copy-only.

**Notifications/tray:** native notification area adapter (pystray or Win32) may be packaged separately from UI; avoid Cocoa/Qt/hidden tkinter dependencies. Notify only in user's interactive logon session.

**Launch/autostart:** per-user Start Menu and Startup shortcut, no registry run key/system service/admin UAC. `uv tool` installed interpreter path used in launcher. No Windows compatibility guarantee from running SSH commands alone; input/focus tests require logged-in interactive desktop.

## Focus-safe output policy

| Context | Default copy | Automatic paste condition | Guarantee label |
|---|---|---|---|
| Notes window selected text | yes | User initiates paste into chosen app | `Copied`; app doesn't force another app focus |
| Quick, Windows | yes | Explicitly enabled; foreground target unchanged; integrity permitted; clipboard still matches | `Paste shortcut sent` or `Copied only` |
| Quick, KDE Wayland | yes | Separate opt-in; test uinput backend; current focused target chosen by compositor | `Paste shortcut sent to current focus (unverified)` or `Copied only` |
| Terminal | yes | User-configured Ctrl+Shift+V or explicit manual paste | `Shortcut sent (unverified)` |
| Elevated/UAC/locked screen | if available | never inject | `Copied only / unavailable` |
| Remote desktop / VM | test case | only when interactive focus reliably observed | no blanket guarantee |

**No clipboard restoration v1.** The application must not silently send Enter, Backspace, Delete, or type arbitrary characters into the target. No bypasses for UIPI or privileged secure desktops.

## Input/desktop acceptance checks

- Conflict/denied portal shortcut; rebind, key repeat, rapid double press, interrupted portal session, logout/login, service restart.
- Mouse/keyboard input during STT, switch field during capture, clipboard changed by third app, clipboard manager history, target app exit before paste.
- UTF-8/UTF-16 correctness: Czech/Slovak diacritics, punctuation, line endings, emoji, long strings.
- Windows Notepad/browser/VS Code, elevated editor (should not inject), Windows Terminal. KDE Kate/browser/VS Code and terminal, compositor and portal approval, uinput opt-in and without uinput.
- Locked screen, sleep/resume, multiple displays/different scale, active remote sessions, microphone unplug, notification unavailable.

## Dependencies and fallback hierarchy

1. **Always:** notes and persistent STT/audio capture; if no desktop integration available, show normal app and Copy button.
2. **Shortcut:** portal (Linux), RegisterHotKey (Windows), external shortcut command fallback (Linux).
3. **Clipboard:** platform clipboard service. If unavailable, keep output in secure local session and offer export/copy after desktop restored.
4. **Paste:** optional capability only after successful controlled test and consent. Never blocks STT or notes.
