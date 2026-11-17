# Actual implementation boundaries

**Source of truth:** design decision detail in `design-spec/04_ARCHITECTURE.md` and ADRs, concrete Python code in `src/dictation4me/`. The specification's `R0-R6` roadmap is the target. Unimplemented/untested acceptance gates remain clearly labeled.

## Process model

- `dictation4me service run`: pure-Python per-user resident service; creates singleton lock + token-authenticated localhost JSON RPC, opens SQLite in WAL mode, initializes presets, recovers interrupted jobs, registers platform shortcut, and periodically expires completed temporary results. No NativeHTMLKit window is created here.
- `dictation4me gui`: starts NativeHTMLKit through its **public Python SDK**. Because `Application.start()` reexecutes the backend, service initialization occurs **inside** the `with Application.start(...)` backend role. The GUI has no SQLite writes; calls go through the resident process.
- `dictation4me toggle`: sends a local `capture.toggle` command, launching the resident service on first use if required. This is also the KDE desktop custom-shortcut fallback.
- There is no ordinary remote listener and no HTTP server. Control endpoint is `127.0.0.1` with private token and strict message size. Neither SQLite nor the plaintext credentials are exposed through RPC.

## Data flow / invariants

1. New recording ID is persisted in SQLite before the microphone opens. PCM16 blocks are queued in bounded memory to a private `audio/spool/<id>.part`; writer flushes regularly. Stopping finalizes to `audio/<id>.wav` before any network call.
2. STT job is persisted (`queued` → `running` → `succeeded`/`failed`). An immutable `transcripts` row is created on success. Failed/cancelled jobs retain the recoverable WAV. A note-mode transcript is appended only by compare-and-swap at the captured `edit_seq`, otherwise stays available for manual append.
3. Quick text has an editable copy separate from `transcripts.original_text`. AI operations produce independent `ai_proposals` with source hash and job identity; none automatically replaces a user-edited note. Only explicitly accepted proposals create permanent revisions or new notes. Quick proposals are bound to the creating quick job.
4. `notes.draft` uses an `edit_seq` compare-and-swap and updates the FTS projection. `notes.checkpoint` creates an immutable named revision; accepting `current`/`alternative`/`new-note` follows the proposal rules.
5. Delivering a quick result **first inserts** a delivery-attempt ID and content hash, then performs clipboard ownership/readback and (when explicitly enabled) a simulated paste. Unknown paste outcomes are not automatically retried after restart.
6. `quick_items` and successful quick WAV data follow configurable retention; unresolved audio is not automatically deleted. SQLite backup uses its online-backup API; user-specified export archive is explicitly unencrypted.

## NativeHTMLKit UI

- `assets/notebook.html` and `notebook.css` are local assets loaded from package data; the Core renderer doesn't fetch remote UI resources.
- `gui.py` uses `Document.on`, `get_value`, `set_value`, `set_text`, `replace_html`, `show_dialog`, `close_dialog`, `focus`, and window callbacks from the public SDK. The Markdown preview escapes source text before creating allowed markup; only app-generated DOM fragments go through `replace_html`.
- The UI is intentionally a controlled custom HTML/CSS layout, not a plugin, virtual-DOM or view-spec framework. `nativehtmlkit_ui` is not required, avoiding its early-release fixed-tree/list constraints.
- On small screens the AI inspector becomes a drawer. Desktop keyboard shortcuts Ctrl+N/S/K use `Document.prevent_default` and the SDK event bus.
- The UI poller runs concurrently with events; `threading.RLock` serializes note/quick transitions. SDK calls are documented thread-safe; source UI callbacks should still be validated on actual native platforms.

## Platform-specific risks isolated in `desktop/`

- KDE Wayland uses GlobalShortcuts portal, with manual KDE command shortcut fallback. The backend deliberately does not steal focus or require a hidden NativeHTMLKit window. Clipboard uses `wl-copy`/`wl-paste`; optional uinput-based delivery is explicitly consented, not auto-authorized.
- Windows uses Win32 `RegisterHotKey`, clipboard owner and `SendInput`. Paste checks the captured original top-level foreground HWND; it cannot prove the original text control or caret is unchanged. It never bypasses elevated application security.
- X11 uses Xlib passive hotkey grabs and xclip/xdotool as a best-effort fallback, with separate acceptance requirement. macOS is not implemented.

## Failure model

All internal IPC calls use structured `AppError` codes. Remote error bodies and key values never enter the UI/CLI status text. SQLite owns authoritative saved content. Audio failures leave `.part` files recoverable when possible; jobs are marked interrupted at service restart and **not** replayed automatically. When a resident worker is finishing after shutdown, generation/closing guards prevent late results being applied as valid output.

**Unresolved:** real desktop qualification, packaging on target architectures, API compatibility with the specific configured model servers, shortcut conflicts and microphone latency. The local agent must record these results.
