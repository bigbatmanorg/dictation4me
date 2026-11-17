# Data model, persistence and consistency

## Canonical storage

- **Database**: SQLite via Python `sqlite3`, single service writer, schema migrations, `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `synchronous=FULL` initially (measure performance), `busy_timeout=5000`; do not use unqualified wall-clock time for ordering. `spec/sql/schema.sql` is a runnable baseline contract.
- **Audio**: `data/recordings/YY/MM/<uuid>.wav` or validated alternative; *relative paths* only in DB; never trust a path from an IPC client. Capture starts in private `data/spool/<uuid>.part` with bounded disk size; finalize using fsync + atomic rename, then DB link.
- **Runtime state**: `state/` diagnostic logs and IPC endpoint discovery; isolated from exportable user notes.
- **Config**: `config/config.toml` non-secret settings; secrets in OS keyring or explicit environment variables, never in DB export.

## Entity definitions

| Entity | Key contents | Lifetime |
|---|---|---|
| `notes` | note ID, title, folder, current_revision_id, pinned, archived/trashed timestamps | durable until hard-deleted |
| `note_drafts` | note ID, **working** Markdown, base revision, monotonic edit_seq, updated timestamp | one per note, crash recovered |
| `note_revisions` | immutable content snapshot, `parent_revision_id`, kind, origin job, source hash | durable accepted history |
| `recordings` | mode quick/note, note_id nullable, finalized relative audio path, hash/duration, status and retention | policy-dependent |
| `transcripts` | immutable original STT text, recording_id, detected language, model & creation time | durable while linked; quick expiry allowed |
| `ai_proposals` | source note/revision/edit_seq/hash, prompt preset snapshot hash, output, status | ephemeral until accepted/discarded/retention expires |
| `quick_items` | capture ID, associated recording/transcript, current edited text, state, expiry | temporary unless promoted |
| `jobs` | operation, request generation, status, provider model label, error code, retry lineage | diagnostic/queue retention |
| `delivery_attempts` | capture, text fingerprint, copy/sent/unknown status, target hint | temporary, no target contents |
| `folders`, `tags`, `note_tags` | one-level organization; unique tag names | durable |
| `note_search_docs` + FTS5 | materialized current title/body search projection | recomputable index |

Use UUIDv4 or UUIDv7 IDs everywhere. Store UTC ISO8601 timestamps; use service monotonic clock for debounce and worker deadlines. `note_revisions` rows are immutable after insertion. `note_drafts` is the only place live text is overwritten.

## Draft/revision invariants

1. Every note may have **zero or one** draft. No accepted revision is edited in place.
2. `notes.current_revision_id` must reference a revision belonging to that note (service enforces; optionally DB trigger).
3. The actual current editor content is `draft.content_md` when it exists, otherwise the selected current revision's content. Selecting an alternate revision does **not** discard a dirty draft; prompt for explicit action.
4. A checkpoint inserts revision and updates note.current_revision_id atomically, then either removes draft or rebases it. If process dies mid-transaction, SQLite rolls back all writes.
5. `ai_proposals` are **never** indexed as current note text until accepted. Their source hash/revision/edit_seq is checked before apply.
6. Restoring old content always inserts a **new** revision with `kind=restore` and parent set to previous current revision; history remains auditable.
7. Search index contains current `note_search_docs` projection only. Update transactionally when title or accepted/draft text changes. FTS triggers maintain FTS entries. On integrity discrepancy rebuild index from projection, not notes.
8. Operations that delete a note permanently clean orphaned media using a two-phase garbage-collection queue; never delete the media file before the DB commit establishing no references remains.
9. Do not delete failed/unprocessed `.part` or finalized recordings as part of routine quick retention cleanup. Expose recovery queue and manual purge.
10. Avoid writing full raw transcript or API key into telemetry, operational logs, tracebacks or support bundles.

## Recovery algorithm

At service startup: open DB/integrity check → inspect `recordings` states and disk spool entries → mark previously active jobs `interrupted` → identify orphan finalized files and incomplete `.part` → offer recovery options (salvage when feasible, retry, delete) → verify active note drafts and FTS index. Never automatically resend a remote model request after uncertain outcome. On disk-full, stop recording safely and mark interrupted; preserve any bytes already written.

## Retention defaults

| Kind | Default |
|---|---|
| Notes/revisions | indefinite until explicit trash purge |
| Audio attached to note | indefinite until user removes |
| Quick delivered text | recoverable 24 hours, configurable 0 / 24h / 7d / forever |
| Quick successful audio | delete only **after** transcript is durably committed; retain if user chose 'save audio' |
| Failed/incomplete quick audio | keep until resolved/deleted; visible recovery notification and storage usage |
| Discarded AI proposals | delete after discard and compact per policy; accepted revision remains |
| Request operational metadata | 30 days, configurable; never raw content/credentials |
| Clipboard | OS-managed; app does not promise to clear history or reclaim other apps' copies |

## Backup/export contract

- Export notebook as a portable directory with `manifest.json` + Markdown files + optional WAV files, inside a user-selected destination, with relative-safe names and SHA-256 hashes. No secrets/tokens/logs in export. Use temp directory and atomic rename when filesystem permits.
- Backup native DB via SQLite backup API, not file copy of active WAL; include audio and manifest, schema version and hash verification. Encryption is **not** built in; clearly warn that exported data may be sensitive.
- Restore is explicit and validates version/hash/path traversal/size before writing. Support **restore into a new data directory** first; never silently overwrite current notes.
- Import simple `.md` and `.txt` files as distinct new notes in v1 if implementation straightforward; folders/tags mapping can be deferred.

## Schema migrations

- Migration version is monotonic, applied inside transaction with preflight checks, backup and rollback-on-error. Stale v1 DB from prior code is **not** auto-migrated; old app did not have a compatible notebook DB.
- Schema changes require SQL and migration tests with real SQLite, one-version upgrade and failed-upgrade recovery fixtures.
- FTS5 must be present in the shipped Python SQLite build; if unavailable, launch notes with explicit 'search degraded' mode rather than fail whole app; `doctor` reports it.
