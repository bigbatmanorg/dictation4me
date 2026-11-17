# Product requirements (normative)

## Vision and identities

**Product:** Dictation4me, a desktop-first notebook and cross-application dictation assistant. The **note** is the durable organizational unit; **quick dictation** is a temporary output session. STT and LLM are independent external/local network services configured by the user. Running the app requires no online account, hosted sync, GPU, microphone when using text, or LLM when only dictating.

## User journeys

**J1 — create note by speaking.** Open notebook → New voice note → Record → Stop → persistent recording is finalized → STT → immutable transcript → editable note draft. User may apply preset and accept/reject AI proposal. Audio can be played and deleted independently.

**J2 — jot note by typing.** Open notebook → New note → type Markdown → autosave → search → organize. Works entirely offline, no credentials.

**J3 — dictate into another program.** In target text field press global shortcut → speak → press again to stop → transcription completes without moving foreground focus → press again to copy and request paste, or use auto-deliver when enabled. If not safe to inject, text stays on clipboard and is recoverable. Never send Enter or Delete.

**J4 — rewrite as work product.** Select note or text range → preset (Concise / Friendly / Architecture decision / Agent prompt / Action items etc.) → preview comparison → Accept as current, Save alternative, New note, Copy, or Discard. Edits made while request runs remain authoritative.

**J5 — failure recovery.** Record while offline → save recording → service records error → view Recovery → retry transcription when provider comes back. No audio lost or stale auto-paste.

**J6 — install and diagnose.** Install through uv, initialize config and desktop integration with dry run, test microphone separately from API, inspect capability levels, restore previous operation after crash.

## Must-have functionality

| ID | Normative requirement |
|---|---|
| PR-001 | Create, edit, delete-to-trash, restore, search and archive text notes with editable Markdown source. |
| PR-002 | Single-level folders, multiple tags, pinned/favorite status, recent items, and keyboard navigation. |
| PR-003 | SQLite local persistence with atomic save, crash-recoverable drafts, immutable accepted revisions and backup/restore. |
| PR-004 | Voice recording from a selected microphone with duration/size bounds, visible or audible recording status and explicit consent. |
| PR-005 | Durable audio spool; no destructive cleanup of unfinished/failed captures; recover safely at restart. |
| PR-006 | Configurable OpenAI-compatible STT endpoint, model, key source, timeouts and audio options. |
| PR-007 | Configurable **separate** OpenAI-compatible Chat Completions endpoint and model. |
| PR-008 | Preset CRUD, configurable instructions/language policies, and no unreviewed rewrite that replaces an existing note. |
| PR-009 | Version-aware AI proposals with original/source revision preserved, compare/accept/save alternative/discard. |
| PR-010 | System-wide shortcut behavior with debounce/repeat protection, clear state, cancellation and retries. |
| PR-011 | Clipboard copy works independently of paste injection. Report honest delivery status. |
| PR-012 | NativeHTMLKit Linux KDE Wayland and Windows x64 GUI qualification before respective supported release. |
| PR-013 | Non-blocking network/audio workers; responsive UI and backend; recover from service/UI restart. |
| PR-014 | Secure credential handling, private local files, clear remote-data disclosures and sanitized operational logs. |
| PR-015 | Accessible/keyboard-operable controls, empty states, errors, and performance with several thousand notes. |
| PR-016 | Deterministic installation/uninstallation; user session autostart opt-in; startup is idempotent. |
| PR-017 | CLI diagnostics, service status, test providers, export and recovery operations. |
| PR-018 | All note and model text must render inertly/escaped; an optional read-only Markdown preview must sanitize allowed HTML. |

## Default behaviors

- Main notebook **does not** need to remain open for shortcuts. Resident service autostarts at **user sign-in only if enabled** during onboarding; no root/system service.
- Entering note mode always saves data locally. Quick dictation **does not automatically create a permanent note**.
- Quick dictation keeps successfully delivered text for a configurable **24-hour recovery window**; after successful transcription, its raw audio is removed following explicit cleanup policy. Failed/unprocessed recordings are retained until user resolves/deletes them; surface size usage.
- Quick mode **manual delivery** is the default. Optional auto-deliver can be enabled after real paste-capability test; on Wayland its *paste to current focused target* behavior requires explicit opt-in.
- Original audio for a permanent voice note is retained by default; user may disable retention for future notes and delete audio from individual notes.
- STT is **batch** after recording ends. Streaming/live text and VAD are not required v1.
- AI processing off by default for quick dictation. Available automatically after STT only when preset and data disclosure are explicitly selected.
- Read/write local files in user's profile only. No telemetry or provider usage tracking by default.
- Autosave working draft ~600ms after last input, on blur/note switch/close; revision checkpoint for milestones, not each keypress.

## Presets shipped

`Clean transcript`, `Make concise`, `Friendly message`, `Professional message`, `Architecture decision`, `Extract tasks`, `Coding agent prompt`, `Meeting summary`. A user can duplicate/modify/delete a preset and set a default for note vs quick mode. The model is told to preserve facts, terminology, code blocks, names, and uncertainty; never fabricate a decision that wasn't stated.

## Delivery tiers

**P0 (v1.0 release blocker):** PR-001 through PR-018, both supported platforms, quick/notes distinction, no data loss, clear defaults and backup recovery. **P1:** Markdown preview, note audio player, generated titles, quick history browser if already supported by schema. **P2:** per-app paste profiles, live transcription, additional language services, syncing, cloud backups, attachment images, RAG, collab, agent tools, mobile/macOS. Do not silently promote P2 into v1.

## Explicit non-goals

- Browser-based interface, HTTP service, Electron/Qt/Tk, Node, model hosting, cloud user accounts, vector DB, PDF generation, transcript diarization, synchronized multi-device editing, embedded rich-text editor or advanced audio editor.
- Capturing microphone without explicit user action, global keylogging, access to other application contents, or guaranteed insertion into protected/privileged fields.
- Compatibility with legacy Dictation4me config and packages, or migration of historical data in this clean replacement. **Never overwrite a user's old data silently.**

## Success measurements (not performance guarantees)

- One keyboard shortcut reliably starts/stops capturing without the notebook open on both qualified platforms.
- Successful capture is recoverable after a killed process and network disconnection; no corrupted note after forced shutdown.
- New text notes remain usable without audio devices or configured endpoints.
- Five-minute voice note and 5000-note notebook remain interactive on target development desktops. Measure, don't invent timings.
- A given delivery session never sends more than one automatic paste attempt; retries require a fresh explicit command.
