# Changelog

## 0.2.0 — 2026-11-17

First release of the replacement implementation (NativeHTMLKit notebook + resident service). It replaces the 0.1.x Tk prototype; settings from 0.1.x are not migrated.

### Added
- Resident service with a global shortcut state machine (record, transcribe, copy), durable WAV spool, Recovery, and authenticated local IPC.
- Notebook: folders, tags, pins, archive and trash, Markdown preview, autosave with conflict protection, version history, voice notes, non-destructive AI proposals and custom presets.
- Independent OpenAI-compatible speech-to-text and chat servers; keys from environment variables or the OS keyring.
- Redesigned UI (see `docs/UX_REVIEW.md`): always-visible dictation status, state-driven Quick dictation, tabbed Settings with unsaved-changes bar, key-availability status, empty states, toasts and an anchored "More" menu.
- `credentials.import-env`: move an environment-variable key into the OS keyring so it works when the service starts at login.

### Fixed (found on a real KDE Plasma Wayland desktop)
- Settings could not be saved for plain-HTTP home-network servers (`http://pgx:4000/v1`).
- `Meta`/`Super`/`Win` shortcut modifiers were rejected; shortcuts are now normalized and rebound live without a restart.
- The background service died when the notebook window closed (shared systemd cgroup); it now runs in its own user unit.
- Launchers and autostart pointed at the base Python instead of the virtualenv.
- Microphone: clean error when PortAudio is missing, fallback to the device's native format, tolerance of a single ALSA overrun.
- A silent recording no longer blocks the shortcut (`NO_SPEECH` keeps the audio in Recovery and returns to Ready).
- Portal registration failures now explain the cause (missing desktop entry, KDE-owned binding).
- Notifications work without `notify-send` (D-Bus fallback).
