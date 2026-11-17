# Risk register and tradeoffs

| Risk ID | Impact / likelihood | Mitigation | Owner gate / residual risk |
|---|---|---|---|
| RK-01 Wayland window activation/focus | High / high | No mandatory overlay; non-focus-stealing OS notification, regular GUI; optional Core issue with reproducer | G2: original-field focus restoration **not guaranteed** |
| RK-02 Wayland paste injection permission | High / high | Clipboard-only by default; uinput explicit security opt-in, private daemon, real test | G2: accepted paste may remain unobservable |
| RK-03 Windows UIPI/elevated targets | Medium / high | Foreground checks, copy fallback, no privileged injection | G3: no secure desktop paste |
| RK-04 NativeHTMLKit UI 0.1.1 small dynamic feature set | Medium / high | Direct public SDK for editor/list; custom CSS; no unsupported rich editing | G2/G3: preview only |
| RK-05 Duplicate audio requests/charged API calls | Medium / medium | Job idempotency lineage; manual retry after uncertain outcome | G1/G4: cannot refund completed provider call |
| RK-06 Model rewrites facts incorrectly | High / medium | Preserve raw text, explicit acceptance, correctness prompts, full comparison | G4: AI output not automatically authoritative |
| RK-07 Crash during audio capture | High / medium | Durable spooled audio, incomplete recovery; explicit disk checks | G1/G4: incomplete last buffer can still be lost |
| RK-08 Concurrent edits and delayed AI | High / high | edit_seq, source hash, proposal review, serialized writer | G1/G4: conflict UI required |
| RK-09 User thinks local-first means private from provider | High / medium | endpoint identity, remote warning, separate consent/settings, privacy docs | G4: endpoint may retain data outside app control |
| RK-10 API key leakage | High / medium | secure keyring/env, no plaintext fallback, scrub logs and support bundle | G4: compromised user session is out of scope |
| RK-11 SQLite and FTS index divergence | Medium / medium | materialized search projection + triggers, integrity and rebuild tests | G1/G4: degraded search recoverable |
| RK-12 Hundreds of revisions from every keystroke | Medium / high | one draft, discrete checkpoints, proposal vs revision split | G1: retention bounded |
| RK-13 NativeHTMLKit released wheel distro floor | High / medium | explicit glibc check and packaging tests, supported distro docs | G2: glibc older than 2.39 not supported by current wheel |
| RK-14 Background process lifecycle duplication | High / medium | OS user single-instance lock, authenticated IPC, idempotent start | G1/G4: no ghost clipboard listeners |
| RK-15 Clipboard manager keeps sensitive text | Medium / high | visible warning, recovery controls, no false cleanup promise | G4: OS outside app control |
| RK-16 Overengineering into plugin/agent platform | Medium / high | fixed tiny interfaces, scope gates, no generic extension system | roadmap approval |
| RK-17 Installer silently changes system permissions | High / medium | dry-run, per-user setup by default, explicit opt-in sudo/uinput | G4: install cleanly without elevation |
| RK-18 User interprets `Paste shortcut sent` as successful | Medium / high | exact language, explicit copy/paste status separation | G4: input destination remains unverified |

## Non-blocking explorations

Future: selected-text rewrite from another application, macOS, rich native editor, optional native quick overlay, streaming STT, input dictation profiles, cross-device sync. These are not needed to qualify `1.0.0` and cannot be implemented as shortcuts around platform security restrictions.

## Upstream NativeHTMLKit request criteria

Only create upstream issue when (1) a public SDK feature is objectively missing or broken, (2) minimal reproducible sample using released package exists, (3) exact OS/version/expected-observed/test evidence is recorded, (4) app-level SDK-only workaround is impossible or unreasonable, and (5) downstream app still works with documented fallback where feasible. Never silently change Core or SDK as part of this app's routine work.
