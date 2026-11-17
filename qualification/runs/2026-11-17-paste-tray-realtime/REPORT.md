# Run 2026-11-17 — two-press paste, tray indicator, realtime + live window, actionable errors

| Check | Environment | Result | Evidence |
|---|---|---|---|
| KDE RemoteDesktop consent dialog, "Allow restoring on future sessions"; token stored 0600 | linuxDEV, Plasma 6.6 Wayland | PASS | `kde-remote-control-consent.png` |
| Second authorization silent (restore token) | linuxDEV | PASS | console |
| Two real shortcut presses → text typed into focused KWrite; ×3 in a row, one portal session | linuxDEV | PASS | `three-two-press-pastes.png` |
| Tray: idle microphone, red record dot while recording, menu (Open / Start / Quit), left click opens notebook | linuxDEV | PASS | `tray-idle.png`, `tray-recording.png`, `tray-menu.png` |
| Realtime with live window: words stream into the window; 2nd press closes it, focus returns, text pasted | linuxDEV (mock realtime server) | PASS | `../../../docs/screenshots/live-window.png`, `realtime-pasted-into-kwrite.png` |
| Real pgx `asr-realtime` (Voxtral) through the engine and recorder tap: 11 live updates, final 0.96 s after stop, transcript stored as `asr-realtime` | workstation k4 → pgx | PASS | console |
| New window focus behaviour: a NativeHTMLKit window takes keyboard focus on KWin; closing it returns focus within 0.6 s | linuxDEV | Observed | basis for "close live window before paste" |
| Realtime protocol, auth failure, resampling, engine fallback to batch | pytest (local websocket fake) | PASS | `tests/test_realtime.py`, `tests/test_engine.py` |
| Actionable CLI errors ("How to fix: …"), doctor fixes | k4 | PASS | console |
| Auto-paste consent on Plasma 6.3 (user workstation) | k4 | NOT RUN (needs the user to approve the dialog) | |
| Windows tray / paste | — | NOT RUN (tray is Linux-only) | |
