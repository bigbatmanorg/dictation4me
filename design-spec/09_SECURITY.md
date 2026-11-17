# Threat model, security and privacy

## Assets and boundaries

**Assets:** microphone recording, raw transcript, typed note text, AI response/revisions, API credentials, local DB/media/backup, clipboard output. **Boundaries:** user consent → microphone, app → configured provider over network, desktop service → GUI over authenticated local IPC, clipboard → other apps / clipboard manager, filesystem → user backup destination.

## Threats and mitigations

| Threat | Required mitigation |
|---|---|
| Accidental background microphone activation | Microphone only opens on explicit user command; clear status/cue, ability to stop/cancel, no hidden continuous capture |
| Loss of recorded speech on crash | Bounded durable spool, fsync, atomic finalize, recovery queue; no automatic deletion of failed audio |
| Transcript/audio sent to wrong endpoint | Separate STT/AI profiles; show destination host/model and remote warning; user-triggered STT/AI only; no implicit cloud sync |
| Malicious AI output/prompt injection | Note content is untrusted data, never execute code/shell, never follow hidden instructions or load external resources; escape/sanitize renderer |
| Local file traversal in media/export | DB stores validated relative media IDs, canonical root enforcement, no arbitrary IPC path; zip-slip protection on restore |
| API key exposure | OS keyring or environment variable; no plaintext fallback; redacted settings/logs, never append key to URL, do not display in support bundle |
| Forged local IPC calls | Unpredictable token, private file permissions, loopback-only binding, size/rate limits, fixed command allowlist, message authentication, single instance |
| Clipboard changes between copy and paste | Verify ownership/fingerprint where possible; skip auto-paste if mismatched; no automatic clipboard restoration |
| Unauthorized key injection | No privileged bypass; default copy-only on Wayland, explicit opt-in to uinput; windows elevated/UAC no injection |
| Accidental note overwrite by slow AI | source revision/edit_seq hash guard; proposal must be accepted, no auto-replace, stale reply ignored |
| Sensitive data in backup | No keys in export; private temp dirs; warn about unencrypted exports; optional user-supplied encrypted archive outside v1 |
| Denial of service via huge note/API response | Size/duration/concurrency limits, bounded JSON/IPC payloads, streaming audio spool, provider timeout, memory pressure tests |
| sqlite corruption/backup inconsistency | single writer, transactions, WAL, integrity checks, SQLite Backup API, restore verification |
| Logs include note content | Structured error codes only, strip URLs credentials/headers/bodies; opt-in diagnostic attachments reviewed before export |

## Clear statements to users

- 'Local-first' means note/recording storage and control are local. **Configured STT/LLM servers may be remote and receive audio/text.** Do not imply the data stays offline when external endpoints are configured.
- Clipboard is controlled by the OS; clipboard managers/history, remote desktops or a pasted target application can retain text outside Dictation4me.
- Encrypted storage at rest is **not** guaranteed by SQLite or private directories. Document reliance on OS profile/disk encryption. Do not invent an 'encrypted' badge.
- No attempts to capture passwords, browser tabs, content of other apps or system-wide key events beyond approved hotkey activation.

## Credential plan

- `keyring` uses OS-approved backend when present. Verify backend is a real secure store, not plaintext/null. On KDE, KWallet/SecretService availability depends on desktop/session packages; if absent show explicit actionable error and permit environment-variable configuration.
- Use separate credential IDs for STT and LLM; allow empty key where endpoint intentionally accepts none.
- Environment variable inherited by user-sign-in process may differ from terminal environment; `doctor` checks presence without printing value.
- Store configuration **references** to keys, not values, and never copy secrets into export or debug bundle.

## Data deletion & retention

Permanent note deletion is explicit and cascades DB data plus media cleanup after DB commit. Users can clear quick sessions, retained audio, AI proposals, local transcripts, notes trash and logs separately. Recovery data must be visible and removable. Uninstall defaults to **preserve user data**, `uninstall --purge` is separate confirmed irreversible action.

## Adversarial tests

- Malicious note with `<script>`, `<img src='http://...'>`, Unicode control chars, injected prompt asking agent to reveal credentials; remains inert plain content.
- IPC unauthorized/auth guess/overlength/symlink/relative path/traversal; reject with no file changes.
- 413/500/timeout/malformed provider response and long output; no secret logging and no draft overwrite.
- Permission denial, storage full, ransomware-like read-only directory, service crash; data recoverable or clear error.
- Linux uinput access intentionally absent; copy-only mode remains functional.
