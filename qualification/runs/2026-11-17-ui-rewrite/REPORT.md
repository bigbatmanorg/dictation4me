# Run 2026-11-17 — notebook UI rewrite

Rewrite of `assets/notebook.html`, `assets/notebook.css` and `gui.py` following `docs/UX_REVIEW.md`. Environment: linuxDEV, Ubuntu 26.04, KDE Plasma 6.6.6 Wayland, NativeHTMLKit Core 1.0.3 via Python SDK 0.1.x. Input was driven with real uinput events (ydotool); the provider was a loopback mock (no secrets on the VM).

| Check | Result | Evidence |
|---|---|---|
| Notes list, editor, pin toggle with toast | PASS | `notes.png` |
| AI & history inspector closed by default, opens on request; toolbar collapses to icons via container query | PASS | `notes-ai-panel.png` |
| Anchored "More" popover menu (no JavaScript) | PASS | `note-more-menu.png` |
| Move to Trash closes menu, shows banner with Restore / Delete forever | PASS | `note-trashed.png` |
| Quick dictation: single state-driven button: Start, then Stop & transcribe (Cancel visible), then Copy to clipboard; real PipeWire capture; clipboard contained the transcript | PASS | `quick-recording.png`, `quick-ready.png` |
| Sidebar dictation card mirrors the service state on every screen | PASS | same screenshots |
| Recovery empty state | PASS | `recovery-empty.png` |
| Settings tabs, unsaved-changes bar, Discard, Save persisted to config.toml | PASS | `settings-unsaved.png`, `settings-dictation.png` |
| Narrow window (760px headless render): icon sidebar, no unsupported-CSS diagnostics | PASS | `narrow-760px.png` |
| GUI contract tests (headless SDK harness) | PASS (10) | `pytest tests/test_gui_contract.py` |
| Screen reader, IME, 125/150% scaling, Windows | NOT RUN | |

Before screenshots: `before-*.png`.

Finding during the run: the Core renderer ignores document CSS `fill`/`stroke` inside inline SVG. Icons therefore carry presentation attributes (`fill="none" stroke="currentColor"`), and state colour is applied through `color`.
