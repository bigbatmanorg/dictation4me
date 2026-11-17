# Run 2026-11-17 — KDE Plasma Wayland end-to-end troubleshooting

Base commit `97dd32c` plus uncommitted working-tree fixes listed below. Python 3.14.

## Environments

| Env | OS / desktop | Use |
|---|---|---|
| Workstation `k4` | Debian 13, Plasma 6.3.6 Wayland, xdg-desktop-portal-kde 6.3.5, PipeWire 1.4.2 | Real microphone, real LiteLLM provider (`http://pgx:4000/v1`, `whisper-1`, `brain-direct`), real Wayland clipboard, GUI-to-service lifecycle |
| linuxDEV | Ubuntu 26.04, Plasma 6.6.6 Wayland, portal GlobalShortcuts v2 | Real NativeHTMLKit GUI driven by uinput (ydotool), real KDE portal shortcut key presses, PipeWire null-sink audio, loopback mock provider (no secrets on VM) |

## Defects found and fixed

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Saving settings fails | `http://pgx:4000/v1` rejected: single-label LAN host treated as public; one invalid field aborts the whole save | `config.is_local_host`: single-label names, `.lan/.home/.internal/...`, and names resolving only to private IPs are local |
| 2 | `Meta+L` shortcut rejected | Validator only knew `super` | `normalize_shortcut`: ctrl/control, alt, shift, meta/super/win/logo to canonical `Ctrl+Alt+Shift+Meta+KEY`; Meta mapped on portal/X11/Win32 |
| 3 | Shortcut change needs service restart | No live rebind | Engine rebinds on save |
| 4 | Shortcut dead after closing notebook | Service was spawned inside the NativeHTMLKit host's systemd unit and killed with its cgroup | Service starts as its own `systemd-run --user` transient unit; env passed by name only (`--setenv=NAME`) |
| 5 | Menu launcher/autostart fail: `No module named dictation4me` | `Path(sys.executable).resolve()` followed venv symlink to base Python | Use `absolute()` |
| 6 | Microphone unusable | PortAudio missing raised `OSError` (uncaught); raw ALSA `hw:` device rejected 16 kHz; single ALSA overrun aborted recording | Clean `AUDIO_UNAVAILABLE`; format fallback to device native rate/channels; overruns counted, not fatal. Host: installed `libportaudio2`, `pipewire-alsa` |
| 7 | Portal shortcut "unavailable" with no reason | Newer portals require an installed `.desktop` entry for host app IDs | Precise error: run `dictation4me init` |
| 8 | Changing shortcut in app has no effect on KDE | KDE keeps the user's stored binding; app value is only the preferred trigger on first registration | Status shows actual binding and where to change it |
| 9 | Errors invisible | Only a 10 px footer | Feedback beside Save, sticky Settings header, live shortcut status, `state/gui.log` for unexpected exceptions |
| 10 | Silent hotkey presses while busy/ERROR | No feedback | Desktop notification (gdbus fallback when `notify-send` absent) |
| 11 | "Disconnected" pill stuck after service restart | Redraw only on state change | Reset on disconnect |
| 12 | `doctor` false issue | Checked non-dependency `nativehtmlkit_ui` | Removed |

## Results

| Check | Env | Result | Evidence |
|---|---|---|---|
| `pytest -q` (83 incl. 18 new regressions) | k4, linuxDEV | PASS | console |
| Wheel build + clean venv install + service start/stop | k4 | PASS | console |
| Real STT `whisper-1` (minimal + openai-modern) / AI `brain-direct` via CLI and via resident service | k4 | PASS | console |
| Real mic capture through PipeWire (3 s ≈ 3 s) | k4 | PASS (after host packages) | console |
| Engine end-to-end with real providers + Wayland clipboard: record, transcribe, deliver, no silent re-deliver, quick AI, voice note append, note AI proposal | k4 | PASS (10/10) | console |
| Service survives notebook close (host unit stopped) | k4 | PASS | systemd units |
| Settings typed via real input and saved (pgx URL, models, env key, `meta+l` normalized) | linuxDEV | PASS | `linuxdev-settings-saved.png` |
| Portal consent dialog + registration | linuxDEV | PASS | `linuxdev-portal-consent-dialog.png` |
| Global shortcut real key presses: record, stop, READY, deliver to clipboard, new session | linuxDEV | PASS | ~200 KB WAV received by mock |
| Shortcut works after closing notebook with window X | linuxDEV | PASS | console |
| KDE binding-ownership status | linuxDEV, k4 | PASS | `linuxdev-shortcut-status.png` |
| Global shortcut real key press on workstation | k4 | NOT RUN (no input injection on the user's desktop) | |
| Login autostart with real session | k4 | NOT RUN | |
| Wayland synthetic paste (ydotool), IME, screen reader, HiDPI | — | NOT RUN | |
| Windows x64 | — | NOT RUN | |
| `ruff check` | k4 | FAIL (14 pre-existing style findings: unused locals, E701, B018, B023, B904) | |

## Incident

At 01:07:53 an exploratory `org.kde.KGlobalAccel.globalShortcutsByKey` D-Bus call from the agent made `kwin_wayland` abort (libdbus assertion) on k4. The compositor restarted; `plasma-ksmserver` and `plasma-powerdevil` were restarted manually by the agent; the user's Claude desktop app exited and was not relaunched. The application does not call KGlobalAccel.
