# Implementation roadmap, dependency order and backlog

The product generation is called **Dictation4me 2.0**. Implement as a clean-slate source replacement. Version the newly built package explicitly (`0.2.0-dev`, `0.2.x`, etc. while implementing, `1.0.0` only after G4); old `0.1.0` config/CLI are not compatibility constraints. One engineering agent may execute phases autonomously, but each phase must have passing gates and signed evidence.

## Dependency graph

```
R0 Research+contracts -> R1 Storage/service -> R2 Notes UI -> R3 Audio/STT
                                                       |             |
                                                       v             v
                                             R4 AI presets       R5 Quick desktop
                                                       \             /
                                                        v           v
                                                       R6 Hardening + release
```

R1 must precede both desktop shortcut and UI work because it owns persistence and recovery. Windows native acceptance begins after Linux service logic is stable; Windows-specific adapters may be developed in parallel once contracts are fixed. No upstream NativeHTMLKit feature work until a demonstrable blocker exists.

## R0 — repository replacement / architecture freeze

**Tasks:**
- `R0-01` Inventory current repo and record which files are retired. Keep repo identity, LICENSE (unless deliberately changing license), and history; replace old package internals, old docs and old tests as needed.
- `R0-02` Add `AGENTS.md`, design docs, ADRs, fixtures, acceptance matrix; verify links and SQL/schema contracts.
- `R0-03` Create clean `pyproject.toml`, uv lock, CI skeleton and package/CLI entrypoints; no old Tk dependencies unless independently justified.
- `R0-04` Confirm public NativeHTMLKit version/API and real Linux glibc/Windows wheels; record actual versions to qualification manifest.

**Exit:** G0, clean importable package with no unsupported architectural assumption. **No claims of functional UI/STT** at this stage.

## R1 — persistence, service and text-only notebook backend

- `R1-01` DB migrations, single-writer repository, note CRUD, folder/tags/trash and revisions/drafts compare-and-swap.
- `R1-02` SQLite FTS5 projection/triggers, pagination and integrity/rebuild.
- `R1-03` Authenticated bounded local IPC, single-instance lock, service lifecycle, CLI status/doctor.
- `R1-04` Async job executor, generation cancellation, idempotent command reducer and explicit error codes.
- `R1-05` Backup/export/restore and startup recovery, crash/failure injection.

**Exit:** G1 storage part; 5000 notes fixture, crash recovery and export restore pass without GUI/provider.

## R2 — NativeHTMLKit notebook UI (text-first)

- `R2-01` Main window, compact cobalt theme, note/folder sidebar, paginated summaries, search, tags/trash.
- `R2-02` `<textarea>` Markdown editor via **public SDK**, autosave to service, save/connection statuses, keyboard shortcuts, basic Markdown preview sanitation.
- `R2-03` Note version timeline, restore/checkpoint, modal confirmations, empty/error states and accessible labels.
- `R2-04` Settings pages (providers with no secret leaks, audio device, privacy, output) and file dialogs.
- `R2-05` Headless SDK test plus **real KDE Wayland input/IME/resize** acceptance. Fix layout defects in app CSS, not Core internals.

**Exit:** functional offline local notebook with reliable editing and search; no microphone required. Retest if NativeHTMLKit runtime updates.

## R3 — audio capture and STT

- `R3-01` Audio recorder device discovery, spool bounds, fsync/atomic finalize, crash recovery and playback worker.
- `R3-02` Minimal STT HTTP client with user-configured URL/model/optional key, real WAV upload, mock contract cases.
- `R3-03` Voice note append/insert conflict behavior, immutable raw transcript and recoverable errors.
- `R3-04` Microphone/remote-provider onboarding and explicit test actions; real KDE physical mic acceptance.

**Exit:** stable note voice recording, recovery from offline STT, no data loss. Provider failure does not block notes editing.

## R4 — AI transformations and version UI

- `R4-01` Chat Completions model client, cancellation, request validation, safe prompt/data separation.
- `R4-02` Preset seed/edit/delete, preview/diff and compare, Accept Current/Alternative/New Note/Copy/Discard.
- `R4-03` Stale result detection via note edit sequence and hashes; persist proposal, metadata and accepted history.
- `R4-04` Language/content fidelity, injection/HTML safety and multi-provider tests.

**Exit:** all core transformations work without overwriting manual edits; incomplete model output never auto-accepted.

## R5 — resident quick capture / cross-app output

- `R5-01` One-session hotkey reducer and quick retention/recovery queue; no GUI dependence.
- `R5-02` KDE portal + external-command fallback, notifications/tray/audio cues, clipboard-only then opted-in uinput paste.
- `R5-03` Windows RegisterHotKey/clipboard/SendInput adapters, foreground checks, elevated/secure desktop copy-only.
- `R5-04` Manual/auto-deliver policies, per-command nonce, clipboard race check, explicit retry and `Save as Note` promotion.
- `R5-05` Both actual desktop qualification matrices; no unsupported focus restoration claims.

**Exit:** G2 and G3. Both targets reliably capture and **copy**; paste capability separately recorded per OS and permission state. Fix normal copy/dictation if paste support is blocked.

## R6 — hardening, packaging and release

- `R6-01` Security audit of IPC/auth/keyring/path traversal, redaction, prompt injection; adversarial fault injection.
- `R6-02` Privacy/retention cleanup and backup/restore with audio, robust installer/doctor/uninstall.
- `R6-03` Responsiveness and resource tests, 100 UI lifecycle cycles, 5000 notes, non-English Unicode, accessibility and scaling.
- `R6-04` CI, uv packaging for actual supported Linux/Windows targets, build provenance, documentation, release notes.
- `R6-05` Complete acceptance ledger and recorded evidence; no P0/P1 data-loss/high-risk defects, explicit known-limitations list.

**Exit:** G4 and `1.0.0` release candidate. Publishing to PyPI/GitHub Releases only after explicit release authority in actual agent assignment; committing/pushing tested code can be part of approved development workflow.

## Issue priority definitions

- **P0:** lost/overwritten user content/audio, insecure credential/IPC, automatic unintended paste, microphone starts without user request, persistent note corruption or app cannot start.
- **P1:** broken normal note/quick workflow, no copy, failed crash recovery, missing real platform acceptance, major keyboard accessibility, incorrect version preview.
- **P2:** non-critical styling, optimization, optional tray quirks, extra preset formats, future platform support.

Block release for P0 and unresolved P1 acceptance gates. P2 may be tracked and documented.

## Anti-scope-creep decision test

Before new feature or dependency: Does it make capture/edit/transform/deliver easier **today**? Can it be delivered by standard Python and existing public NativeHTMLKit APIs? Is it necessary for G4? If no, move to post-v1; do not add plugins, extension registry, agent orchestration, embedded HTTP, vector DB, cloud sync or custom rendering engine.
