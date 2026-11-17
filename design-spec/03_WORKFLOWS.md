# Deterministic workflows and state machines

## Scope and ownership

A **CaptureSession** is one recording + processing + optional delivery. Exactly one live microphone recording may be active **across quick and note modes**; starting a second is rejected with a clear busy state. A note recording is created from a specific note and is never automatically delivered to another app. Audio capture, STT and LLM tasks run in workers; **one serialized controller** is the authority for state and request generation. Each command has a unique correlation ID and is idempotent within the active session.

## Quick capture states

```
             hotkey              hotkey
IDLE ----------------> RECORDING -----------> FINALIZING
 ^   (no window)           | cancel            | durable file commit
 |                         v                   v
 |                     CANCELLED          TRANSCRIBING
 |                                             |
 |                               +-------------+------------+
 |                               v                          v
 |                         TRANSFORMING                  READY
 |                               |                          |
 |                               +------------> READY <-----+
 |                                               | hotkey / explicit deliver
 |                                               v
 |                                            DELIVERING
 |                                               |
 +----- explicit finish / new session <---- RECOVERABLE_RESULT
                   \                             |
                    \--------------------------- ERROR / RETRY
```

**State distinctions:** `IDLE`, `RECORDING`, `FINALIZING`, `TRANSCRIBING`, `TRANSFORMING`, `READY`, `DELIVERING`, `RECOVERABLE_RESULT`, `ERROR`, `CANCELLED`. `RECOVERABLE_RESULT` records *clipboard_copy_success*, *paste_shortcut_sent* and *paste_outcome_unknown* independently. Never treat delivery as proof of field acceptance.

### Global hotkey semantics (single shortcut)

| Current state | Key pressed | Required behavior |
|---|---|---|
| IDLE / finished session | start new recording | Open selected microphone only; initialize disk spool; show notification or audio cue; do not focus GUI |
| RECORDING | stop recording | Flush/finalize audio, close microphone, start STT; no clipboard or paste yet |
| FINALIZING / TRANSCRIBING / TRANSFORMING / DELIVERING | *busy* | Ignore/notify 'still processing'; never enqueue another recording, retry or paste. No hidden queue |
| READY | deliver current text | Copy text; optionally send one paste command subject to platform policy and clipboard check. Mark result recoverable |
| RECOVERABLE_RESULT | start next capture | New session begins; explicit `copy-last` / `paste-last` remain available |
| ERROR with durable recording | no implicit action | Show Recovery, retry explicitly or cancel. Never delete audio or paste stale text |
| CANCELLED | start new capture | No stale callbacks may mutate/deliver previous session |

`Esc` in **focused** review window cancels its current operation after confirmation when capture has material; global `dictation4me cancel` is an explicit CLI/tray action. Repeated global shortcut events have debounce and OS auto-repeat suppression; key release is required before another transition. Cancel during network request is best effort, but delayed replies are ignored via generation token. Recording cancellation preserves finalized or partial audio until the user explicitly chooses **Discard**.

**Alternative mode:** setting `auto_deliver=true`: when READY is reached, perform *exactly one* copy-and-conditional-paste attempt. Must be explicitly enabled after output integration test and distinguish **copy success**, **key event sent**, **target accepted (not observable)**. On KDE Wayland it requires separate explicit *paste-to-current-focus* consent; otherwise default clipboard only.

**Target semantics:** Before recording, service may capture platform-permitted foreground target metadata (Windows HWND/process; Wayland none). It never stores actual content from target apps. Revalidate immediately before paste if possible. For Windows, if foreground target changed, skip automatic injection and copy only. On KDE Wayland, cannot guarantee original field; do not claim original-target paste, even if `ydotool` sent Ctrl+V.

## Note recording workflow

1. User selects note and presses Record; note ID and editing generation are captured.
2. Record into private spool (not RAM only) with max duration/size and permission feedback.
3. Finalize audio atomically and link recording in database transaction.
4. Start STT job; persist result as immutable `transcripts` record on success.
5. Create **proposal to append/insert** transcript in working draft; do not replace unrelated manual edits. Insert at original position only if draft edit sequence matches; otherwise offer Append / New note / Review.
6. AI transformation is independent and optional. User chooses preset and accepts the proposal.
7. Original recording and transcript stay attached unless user explicitly deletes audio or note.

## Manual note editing

- UI sends `{note_id, base_edit_seq, text}` debounced ~600ms via IPC; service serializes writes; returns new edit sequence and commit timestamp.
- On note switch or GUI close request, request `flush_draft` and wait for ACK or surface a persistent saving error. Never claim saved before ACK.
- `note_drafts` tracks unsnapshotted typing; on explicit AI acceptance, note switch/close, or periodic checkpoint, transactionally create immutable `note_revisions` row and point the note's current revision at it, resetting draft base. Avoid saving a new accepted revision for every keypress.
- If edit sequence differs, service returns `CONFLICT`. GUI keeps unsaved content in a local emergency buffer and shows reconciliation instead of overwriting the database.
- Crash recovery reconstructs the latest durable draft. Service restart does not depend on GUI.

## AI transformation safety

1. Snapshot **exact** text/range plus note ID, `edit_seq`, revision ID and source SHA-256. Set max content size, show which endpoint receives it.
2. Execute separately from UI callbacks. AI returns **proposal** only; validation confirms non-empty output, length limits and safe plain text/Markdown.
3. If user has edited note while waiting, proposal remains in panel and is marked *source changed*. No auto-replace.
4. Accept as Current creates a revision derived from captured parent. For a selected range, apply only if current draft source hash/selection context matches; otherwise require explicit alternative/new note.
5. Save Alternative creates accepted revision without selecting it; `New Note` is a separate notebook entry linked to source. Discard deletes pending proposal but leaves previous revisions.
6. Regenerate starts new job; old job responses are marked superseded and cannot alter current preview.
7. Prompt output is untrusted text. Do not execute code, open links or invoke actions in it automatically.

## Delivery and clipboard race

1. Choose a concrete text version and freeze it for the attempt; all later edits are separate.
2. Write Unicode string to clipboard; if failing, mark error, keep app copy.
3. Read clipboard back (where supported) or track ownership and verify fingerprint before sending shortcut. If changed, **never paste**.
4. Confirm delivery mode; dispatch configured key shortcut only once. Record `paste_shortcut_sent` if injection API reported success, not `pasted`.
5. When target verification is unavailable or privileged/protected focus is detected, use clipboard only and warn. Do not synthesize Enter/Backspace/Delete.
6. Do not automatically restore previous clipboard. Restoring may race with user or target's clipboard manager. A future optional restore would require separate design/qualification.
7. Retry Paste is a **separate explicit action** with new attempt ID; if uncertain, warn about duplication.

## Crash matrix

| Failure moment | Required recovery |
|---|---|
| During recording | Detect and preserve `.part`; recover/salvage PCM if possible; never claim full recording; label incomplete |
| After finalize but before STT | Recovery queue shows audio ready to transcribe |
| During STT | Job becomes `interrupted` after restart; manual retry, no automatic duplicate request |
| After STT returned but before note creation | Transaction saves transcript/job and note linkage together or job remains recoverable |
| During AI call | Existing draft/revisions untouched; interrupted proposal may be retried |
| Clipboard copied but before paste | Persist 'copied' state; no automatic replay after restart |
| During paste injection | Persist 'outcome unknown'; never auto-replay |
| UI exited while service busy | Service continues; new GUI can reconnect and inspect status |
| Service exited while UI open | UI displays disconnected state, retains unsent drafts, restarts/connects service with explicit feedback |
