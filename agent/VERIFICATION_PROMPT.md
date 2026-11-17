# Independent read-only adversarial audit of the implementation

Review the **existing** Python/NativeHTMLKit Dictation4me 2.0 code, not its obsolete Tk prototype. Read `AGENTS.md`, `README.md`, `docs/ARCHITECTURE_IMPLEMENTED.md`, `docs/IMPLEMENTATION_STATUS.md`, `design-spec/`, `contracts/` and `qualification/acceptance.csv`. Start read-only; do not modify any source or data during the initial audit.

Inspect `engine.py`, `storage.py`, `audio.py`, `gui.py`, `providers.py`, desktop adapters, IPC, backup/restore, config, packaging and tests. Verify the hotkey state machine, persisted delivery intent before keyboard injection, audio crash recovery, immutable raw transcripts and accepted revisions, source-hash CAS protections, GUI edit conflicts and notes deletion, retention policy, keyring/remote consent, Unicode, model API compatibility, no secret logging and safe Markdown rendering.

Run safe tests in `DICTATION4ME_HOME`-isolated profiles. Native GUI, real microphone, KDE shortcut portal, Wayland focus, Windows clipboard/SendInput/UIPI, IME/scaling require separate **real interactive** proof. Do not treat a successful mock, Xvfb or SSH job as native acceptance.

Report issues by severity (P0/P1/P2), file/line, reproduction, user impact, recommended minimal fix and regression test. Classify every relevant acceptance gate PASS, FAIL, BLOCKED or NOT RUN with evidence. **Do not claim a release is qualified from source inspection alone.** When later asked to fix, patch the source minimally, rerun tests and update evidence.
