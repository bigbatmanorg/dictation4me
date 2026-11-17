# Changelog

## 0.2.0 — 2026-11-17

First release of the rewritten Dictation4me: a background service with a native notebook, replacing the 0.1.x Tk prototype. Settings from 0.1.x are not migrated.

### Dictation
- **Two-press dictation into any app:** press the shortcut to record, press again to stop. The text is typed into the focused app (KDE Remote Control portal, approved once), copied to the clipboard, or held for review, as you choose.
- **Live transcription:** stream audio to an OpenAI-style `/v1/realtime` endpoint (e.g. Voxtral on vLLM behind LiteLLM) and watch the words appear in a small live window. The recording is always saved; the batch model takes over if the stream fails.
- Global shortcut through the KDE GlobalShortcuts portal (X11 key grab and Windows RegisterHotKey adapters included); `dictation4me toggle` for custom bindings.
- Silent recordings no longer block the shortcut: "No speech detected", audio kept in Recovery.

### Notebook
- Notes with folders, tags, pins, archive and trash, Markdown preview, autosave with conflict protection and full version history.
- **Voice notes** with live text inside the editor; the transcript is appended on Stop (your own typing always wins).
- **AI rewrite** with eight editable presets plus your own: results are proposals you *Replace*, *Keep as version*, save as *New note* or discard.
- Quick dictation page with the last result, Copy again, Save as note and AI rewrite.
- **Recovery** for every failed recording, with the reason and how to fix it.

### Desktop integration
- Tray icon (StatusNotifierItem): recording state, open notebook, start/stop, quit.
- The background service runs as its own systemd user unit and keeps working when the window is closed; optional autostart at login.
- Notifications for every state change, including why pasting failed.

### Setup and troubleshooting
- Settings redesigned after a UX review: tabs, plain language, key-availability status with "Move key to keyring", unsaved-changes bar, live shortcut status that says when KDE owns the key.
- **Every error explains how to fix it**, in the app, notifications, CLI ("How to fix: …") and `dictation4me doctor`.
- Home-network servers over plain HTTP (`http://pgx:4000/v1`, `*.lan`, private IPs) are accepted; internet servers need HTTPS and explicit approval.
- API keys from the OS keyring or environment variables only; never written to the settings file.
- Backup, Markdown export and safe restore into a new profile.

### Fixed during qualification on KDE Plasma Wayland
- Settings could not be saved for plain-HTTP home-network servers.
- `Meta`/`Super` shortcut modifiers were rejected; shortcut changes now apply without a restart.
- The background service died when the notebook window closed.
- Menu and autostart launchers pointed at the wrong Python interpreter.
- Microphone: missing PortAudio, devices that refuse 16 kHz, and a single ALSA overrun aborting a recording.
- AI *Replace note*, *Keep as version* and *New note* failed with "Something went wrong".
- Backup and restore failed on Windows (SQLite handle left open).

### Known limitations
- Verified on KDE Plasma 6 Wayland (Debian 13, Ubuntu 26.04). Windows passes the automated tests but has not been verified on a real desktop; it has no tray icon yet.
- The live window takes keyboard focus while you speak (KDE gives new windows focus); it closes before the text is pasted.
- No macOS support, no cloud sync, Markdown instead of rich text.
