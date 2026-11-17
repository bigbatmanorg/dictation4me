# Implementation evidence and outstanding qualification

Snapshot: **2026-11-17**. This package contains actual source implementation, not only a specification. The release remains **developer handoff / not qualified for production**, and no old-user data is migrated.

## What is implemented

| Component | Code | Locally verified | Native verification |
|---|---|---|---|
| SQLite notes, folders, tags, drafts, revisions, FTS, recording metadata | `storage.py` + `assets/schema.sql` | Python/SQLite tests | N/A |
| Disk-spooled PCM16 WAV and interrupted-file salvage | `audio.py` | Synthetic virtual microphone and WAV tests | PipeWire microphone **PASS** (KDE Wayland) |
| Independent OpenAI-compatible STT / chat HTTP providers | `providers.py` | httpx MockTransport tests | LiteLLM `whisper-1` / chat **PASS** |
| Non-destructive AI proposals, preset CRUD, source conflict checks | `engine.py`, `presets.py` | Engine async tests | Configured model **NOT RUN** |
| Hotkey first/second/third press, cancellation and persisted delivery intents | `engine.py`, `storage.py` | Mock desktop tests | KDE Wayland portal key presses **PASS**; Windows **NOT RUN** |
| Authenticated local IPC, process separation, CLI | `ipc.py`, `service.py`, `cli.py` | Real subprocess service/IPC test | Interactive login lifecycle **NOT RUN** |
| NativeHTMLKit Python notebook, settings, revision/history, quick, recovery UI | `gui.py` + `assets/notebook.*` | Mock public-SDK contract tests | Real window with uinput input on KDE Wayland **PASS**; Windows **NOT RUN** |
| KDE portal shortcut / Linux clipboard and optional injection | `desktop/portal.py`, `desktop/linux.py`, `desktop/x11.py` | Source review, pure logic | KDE portal + Wayland clipboard **PASS**; ydotool paste / X11 **NOT RUN** |
| Win32 hotkeys / clipboard / safe paste attempts | `desktop/windows.py` | Source review, cross-platform tests | Windows GUI session **NOT RUN** |
| Backup/Markdown export, safe restore | `portable.py` | ZIP/SQLite round-trip and attack tests | Non-test user data **NOT RUN** |
| Desktop startup/install scripts | `onboarding.py` | Dry-run test | Actual login/start menu **NOT RUN** |

## Checks executed in source environment

- `pytest -q` → **57 passed** on a Linux Python 3.13 container on 2026-11-17. This count includes HTTP MockTransport, real SQLite, a real loopback IPC service subprocess, virtual microphone WAV data, clipboard mocks, and a simulated NativeHTMLKit SDK document.
- `python -m compileall -q src tests` → PASS, Python 3.13 source and tests byte-compiled.
- Python dependencies needed for real desktop UI were **not installed in this test environment** (`nativehtmlkit`, `sounddevice`, `dbus-next`, `keyring` unavailable). No native desktop screenshots, GUI input, audio device or hotkey acceptance has been performed here. The final installed-wheel test is recorded separately only if actually run.
- There is no `uv.lock` in this archive. Locking and actual platform-qualified dependency resolution remain local-agent tasks.

## Required local-agent steps (do not claim complete until done)

1. Install with `uv sync --group dev` on KDE Wayland (glibc >=2.39) and a Windows x64 interactive desktop. Capture full dependency/build commands and immutable commit IDs.
2. Start/stop service repeatedly, including when the notebook closes, logging in/out, shortcut registration, microphone changes and keyring access; verify settings persist and no duplicate resident process.
3. Use disposable target applications for Unicode clipboard and keyboard injection. Test Wayland without synthetic paste first, then optional explicitly authorized ydotoold; test Windows UIPI/elevated target refusal.
4. Run real STT and AI requests using manually entered endpoint/model settings, plus offline, bad JSON, oversized, timeout, 401, 429, 500 and cancellation scenarios. Do not print token values or private notes.
5. Verify NativeHTMLKit actual rendering and interaction at 100/125/150% scaling, IME input, keyboard shortcuts, dialogs, dynamic list clicks, Markdown preview, tab switching and window close while unsaved.
6. Run `ruff` checks, packaging, clean installed-wheel launch, restore to new home and full CI on both OS targets; repair failures.
7. Record each checked item in a run report under `qualification/runs/` with PASS/FAIL/BLOCKED/NOT RUN; never mark simulated tests as native qualified.

## Known gaps / decisions

- No floating quick overlay; desktop notifications are the fallback because public NativeHTMLKit Wayland window focus/visibility does not currently guarantee one.
- No full rich text; plain-text Markdown textarea with inert preview is intentional.
- Remote-provider approval is explicit, stored in local settings. The agent should verify reconfirmation behavior when provider destinations change.
- No official tray integration in this replacement yet. The resident service is controlled through shortcuts, CLI and the notebook.
- Clipboard paste cannot verify that a target accepted characters, even if the OS reports that the key sequence was sent.
- The archived specifications and acceptance matrix are **targets**, not automatic proof that all 98 external acceptance scenarios have passed.

This status file must be updated by the local agent with reproducible commands and evidence, not with guessed successes.

## Update 2026-11-17 — KDE Wayland troubleshooting run

See [`qualification/runs/2026-11-17-kde-wayland/REPORT.md`](../qualification/runs/2026-11-17-kde-wayland/REPORT.md). Real KDE Wayland GUI, portal global shortcut (real key presses on linuxDEV), PipeWire microphone, Wayland clipboard and real STT/AI endpoints now have PASS evidence. Windows, login autostart, synthetic paste, IME and accessibility remain NOT RUN.

## Update 2026-11-17 — UI rewrite

See [`qualification/runs/2026-11-17-ui-rewrite/REPORT.md`](../qualification/runs/2026-11-17-ui-rewrite/REPORT.md) and [`UX_REVIEW.md`](UX_REVIEW.md). Screen reader, IME, HiDPI scaling and Windows remain NOT RUN.

## Release 0.2.0 — 2026-11-17

Released as v0.2.0 (Beta). Real-desktop evidence: [`qualification/runs/`](../qualification/runs/). Remaining NOT RUN gates: Windows interactive desktop, screen reader, IME, HiDPI scaling, X11 session.
