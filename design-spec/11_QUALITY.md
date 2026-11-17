# Quality strategy and acceptance

## Definition of done (each implementation slice)

Code + unit tests + integration tests + one user-visible scenario + documentation/update to acceptance ledger + type/static analysis + changes summarized. No 'completed' for missing tests, and no test count without command/output evidence. Failures must remain visible with issue IDs, reproduction, impact and next action. No silent bypasses, `except: pass`, fake mocks advertised as native verification or tests disabled to pass.

## Test pyramid

| Level | Coverage | Runs |
|---|---|---|
| Pure unit | reducer/state transition table, note revisions/hash conflict, retention, prompt generation, path validation, settings, input debouncing | Linux + Windows CI |
| Contract | bounded IPC schema/auth/versions/idempotence, STT multipart and JSON, LLM chat payload/response, config, SQL | Linux + Windows CI |
| Service integration | real SQLite (WAL/migrations/FTS), spool fsync/failure injection, crash restore, per-user single instance, backup/restore | isolated temp homes |
| Headless NativeHTMLKit | render and event binding, document update, UTF-8, focus inside controls, source sanitization | `NATIVEHTMLKIT_TEST_MODE=1` (not proof of desktop) |
| Desktop integration | real shortcut, microphone, notifications, clipboard and paste, application focus, IME/reader, sleep/resume | local KDE Wayland and Windows interactive desktop |
| Adversarial | malicious IPC, huge buffers, slow/duplicated responses, keyring unavailable, bad HTML, disk full, crash between atomic steps | isolated test environments |
| Packaging | uv install/uninstall, runtime dependencies and launchers from installed interpreter, no source checkout | clean Linux/Windows user sessions |

## Acceptance matrix

`qualification/acceptance.csv` is executable planning data with `case_id,area,priority,platform,method,scenario,expected,gate`. Tests with `gate=release` are blockers on their listed platforms. Update status in a separate test-run log (`qualification/runs/<date>-<platform>.md`) with actual command/host, code commit, pass/fail, reproduction and logs redacted. Do not overwrite planned expected outcomes with claimed results.

## Required representative acceptance sequences

**A. Normal note:** create, edit with Czech diacritics and Markdown, switch note, kill GUI/service, recover exact final saved text, search current body, restore earlier revision, export and reimport.

**B. Quick dictation:** hotkey once starts recording; twice stops and transcribes; while busy cannot start another capture; at READY once more copies, and only if allowed sends one paste shortcut. If clipboard changed in interval, injection is skipped. Retry explicitly does not remove original.

**C. Failure recovery:** crash during recording, after finalize, while HTTP request pending, after STT but before DB ack, and while note draft unsaved. Check no lost finalized capture, corrupted SQLite, duplicate request or automatic paste after restart.

**D. AI stale revision:** start transform, edit note and apply another preset, return old response last: old proposal remains labeled stale, no edit lost or automatically accepted.

**E. Privacy:** network traffic only to selected endpoints after explicit action; file and token permissions, environment credentials, redacted logs, sanitized Markdown render, no clipboard restoration or unsolicited upload.

**F. Platform:** portal permissions/denial/conflict and KDE external shortcut fallback; Windows hotkey collision and UIPI elevated target. Native tests show actual focus/clipboard output in Kate/Notepad/terminal/browser. SSH-only Windows run is not desktop acceptance.

## Performance budgets (targets to measure, not claims)

- No blocking UI callback on STT/LLM/network/SQLite `backup` operations.
- 5000 notes: note list loads first page without fetching all note bodies; search queries page results; UI remains responsive.
- Continuous microphone capture: fixed memory ceiling and spooled data; 5 minutes audio does not grow Python memory linearly with duration.
- Busy/held shortcut: exactly one state transition per physical press; 1000 rapid/repeated input simulations do not duplicate paste.
- 100 full UI open/close cycles produce no accumulating stale SDK callbacks or resident-process leaks.
- Simulate large note (100KB) and provider 1MB outputs; enforce UI/document command limit.

## Source-of-truth and verification lab

- Primary Linux target: actual KDE Wayland session with microphone/audio, clipboard manager, native shortcut portal, permission dialog, optional uinput. Do not substitute Xvfb for release qualification.
- Secondary Windows target: interactive Windows development VM/session, ideally Windows Server 2022/desktop environment and a normal Windows desktop version; test with real GUI applications. Agent may provision via available SSH, but SSH alone cannot prove desktop keyboard/focus behavior.
- CI Linux/Windows unit/package matrix; integration runs in disposable `DICTATION4ME_HOME`. No test reads/writes user production notes, keyring or clipboard unless guarded by explicit desktop opt-in.

## Quality gates

**G0 design:** docs/contracts consistent; no unresolved P0 architectural decision; SQL/schema/preset fixtures validate.

**G1 storage/service:** state machine, migrations, save/recover, IPC auth, provider mocks all pass; real crash tests.

**G2 Linux:** KDE Wayland shortcut + microphone + clipboard + fallback + real NativeHTMLKit GUI passed; unsupported paste mode identified honestly.

**G3 Windows:** Windows interactive capture, clipboard, native hotkey, UIPI fallback, GUI passed.

**G4 release:** backup/restore roundtrip, Unicode, keyring, installer/uninstall, data safety, negative tests, no P0 defects; source/version/hash and evidence recorded.

## Reporting

Every gate closes with `PASS | FAIL | BLOCKED | NOT RUN`, actual evidence path, host/OS, SDK versions, commit ID and exact limitations. A blocked native test does **not** become a pass because a mock worked.
