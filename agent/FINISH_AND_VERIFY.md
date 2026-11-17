# Autonomous completion and real-desktop qualification prompt

You are the **implementation maintainer, adversarial bug fixer, integration tester, and release engineer** for the **already-implemented** Python/NativeHTMLKit application `bigbatmanorg/dictation4me`. The owner wants an end-to-end working replacement with all backends filled later through the GUI. You have full autonomy to inspect, test, fix and commit within this repository. **DO NOT throw away this implementation or start over**; it already includes real Python code, tests, UI assets, SQLite schema, STT/AI clients, platform adapters and end-user documentation.

## Mission and permissions

- Merge/copy this ZIP's source into the existing `bigbatmanorg/dictation4me` Git checkout **without replacing `.git`**, preserving Git history and unrelated user changes. The former 0.1.x Tk/TTK prototype is obsolete and does not need compatibility. Preserve the license. Do not delete user data/configuration/secrets on any machine.
- Read `AGENTS.md`, `README.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/ARCHITECTURE_IMPLEMENTED.md`, the current `design-spec/` ADRs, and `qualification/acceptance.csv`. Read source/tests before coding; do not treat old specification prompts as instructions to reimplement already-existing parts.
- Make the complete replacement work on the owner's KDE Wayland Linux development desktop and interactive Windows x64 VM. The user will configure STT and AI URLs, model names and credentials through Settings **after** code integration; use local disposable HTTP mock services for provider tests instead of requiring their private tokens.
- The owner has a local Linux workstation with Docker and an optional Windows development VM (`ssh administrator@winDEV`, Proxmox `ssh root@pvez`). Inspect accessibility and authorization before using these. SSH alone is **not** evidence of native interactive desktop input. Do not reset snapshots or system configuration without explicit authorization.
- Implement minimal fixes wherever real tests expose flaws. No handoff requiring the user to debug code manually, no untested success claims. If a platform restriction is real (notably KDE Wayland focus), implement a truthful clipboard-only fallback and document it; don't bypass OS security.

## Execution plan — perform it, not merely describe it

### 1. Integrate source and make clean install reproducible

- Check branch and working tree, preserve changes. Ensure only new replacement files remain; retire old Tk source/tests/docs. Check Python 3.12/3.13, uv, disk and desktop prerequisites.
- Run `uv lock` on a connected machine and commit `uv.lock` only after it resolves. Run `uv sync --group dev`, `uv run pytest -q`, `uv run ruff check src tests`, `uv build`, `uv tool install --reinstall .`, `dictation4me doctor --json`, `dictation4me init --autostart` in isolated profile when testing. Fix all failures and add regression tests.
- Test actual installed wheel imports package assets (`notebook.html`, CSS, presets JSON, SQL schema) and starts the service without referencing the source checkout; verify project metadata, entrypoints, Windows .lnk paths, Linux .desktop entries and login autostart.

### 2. Real end-to-end app behavior

- Open **real** NativeHTMLKit notebook; verify first-use note, create/rename/delete/restore/purge notes, folder/tags/search/pin/archive/trash, autosave on typing and close, Ctrl+N/S/K, preview, multiline Czech/Slovak/emoji/IME, AI drawer, revision timeline, markdown export, backup+restore. Run at 100/125/150% display scale; correct CSS and event issues in **this application** before asking for upstream fixes.
- Attach a physical microphone, record note and quick capture, check private disk spool before network request, stop transcription, failed API response, manually retry after restoring mock endpoint, interrupted session salvage and audio playback.
- Run real local mock STT `POST /v1/audio/transcriptions` and LLM `POST /v1/chat/completions` services with realistic multipart and JSON replies. Configure both entirely through GUI, including model names and optional env/keyring auth. Test bad URL, invalid key, 401/429/500, empty/invalid/oversized responses, latency, network outage and timeout. Test remote consent check; avoid unapproved public network uploads.
- Run repeated global shortcut sequences: first/start, second/stop, busy/no-op, third/copy, repeat/new recording. Test KDE portal and custom command shortcut fallback separately, conflict/missing consent; Windows RegisterHotKey. Verify canceled/stale requests never paste, source-note edits win over stale AI, and no crash causes automatic duplicate paste.
- In disposable Kate, browser, VS Code and terminal fields test clipboard and optional paste. **Do not claim field insertion if only SendInput/ydotool accepted a shortcut.** Wayland auto-paste requires explicit user permission and safe mode; Windows UIPI/elevated/secure targets must fail closed. Verify no synthetic keyboard action or permission change occurs without opt-in.

### 3. Adversarial maintenance review

- Perform line-by-line bug hunt of `engine`, `storage`, `audio`, `ipc`, `gui`, `providers`, `desktop`, `portable`, `config`, `credentials`, `onboarding`. Find/fix data races (service exit while STT in-flight, parallel UI autosave and AI, quick result expiration, rapid shortcuts), filesystem crash consistency, orphan file cleanup, invalid-input handling, remote destination confirmation, stale UI background jobs, Windows HWND lifecycle and Linux shortcut portal error handling.
- Test SQLite integrity and FTS after 5k notes, CAS conflicts, backing up while recording, crash at each delivery/job boundary, malicious Markdown/AI text injection, ZIP traversal/zip bomb/symlinks, token guessing, oversized IPC requests, and bad device IDs. Revisit exact NativeHTMLKit Core API/limitations before claiming support or requiring an upstream change.
- Run no-API-key, env-key, keyring-key provider paths. Ensure routine status logs never include transcripts, raw audio, Authorization headers, keys or full model responses. Save passwords only to approved OS keyring; no plaintext convenience fallback.
- Add tests for every confirmed P0/P1 defect and rerun entire suites until clean. Avoid expanding scope into cloud sync, vector databases, agent frameworks, WYSIWYG editor or plugin infrastructure.

### 4. Independent verification and shipping standard

- Run Linux and Windows CI locally or through test VMs; all mandatory unit/integration/build/real-native gates must PASS before calling v1 qualified. Populate `qualification/runs/YYYY-MM-DD-linux-kde.md` and `...-windows.md` with exact commands, OS, screen/display details, commit, results, blockers and reproduce steps. Existing `qualification/acceptance.csv` defines 98 cases and **does not mean 98 already passed**.
- Test normal user launching from Start Menu/KDE after logout/login. Verify service shutdown cleanly and no orphan owned resources. Install/uninstall preserves data by default. Source ZIP should not be needed after installed tool installation.
- Update README/status/roadmap/ADRs with what actually works. Keep any genuine platform blockers as explicit NOT RUN/BLOCKED and do not invent workarounds. Make changes in coherent commits and push to the authorized development branch; do not publish PyPI or GitHub releases unless explicitly authorized.
- Final report to owner: commits, tested OS/desktop, exact commands/results, external dependency versions, true NativeHTMLKit screenshot/test evidence, issues fixed with tests, remaining blockers, and the shortest instructions to start the app and fill STT/AI in Settings.

## Non-negotiable safeguards

- **Use this code**, not a redesign proposal. Code, tests and docs must remain in repo. Autonomous fix and retest; avoid stopping at an audit.
- No browser or JavaScript, no direct Core protocol, no hidden local HTTP daemon. NativeHTMLKit SDK owns UI; the resident service owns SQLite and microphone.
- Never auto replay uncertain paste or silently lose failed recordings/transcripts; preserve original immutable content. No automatic escalation/uinput/permission changes.
- If native issues are inaccessible over SSH, use a genuine interactive desktop session, not a fake PASS. Do not claim to be finished until you have actual evidence or plainly described blockers.
