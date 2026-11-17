# Local automated verification — 2026-11-17

This evidence is for the delivered Python source archive, not a qualified native desktop release.

## Executed

- `python -m pytest -q` — **57 passed in 2.43s** (Linux, Python 3.13).
- `python -m compileall -q src tests` — PASS.
- `python -m pip wheel --no-deps --no-build-isolation -w /mnt/data/dictation4me-build-check .` — PASS, built `dictation4me-0.2.0-py3-none-any.whl` from the replacement source.
- Isolated wheel smoke test: `pip install --no-deps --target <temp> <wheel>`, `PYTHONPATH=<temp> DICTATION4ME_HOME=<fresh-profile> python -m dictation4me init --no-desktop`, `service start`, `service status`, and `service stop` — PASS. Packaged notebook HTML/CSS, preset JSON and SQL schema were accessible through `importlib.resources`.

## Not executed here

- Dependency resolution on a connected target machine (`uv lock`, `uv sync`, `uv build`, `ruff`).
- Physical microphone, actual STT/AI model endpoints, real NativeHTMLKit window and input, Windows interactive desktop, KDE Wayland global shortcuts, clipboard/paste, high-DPI and IME.
- Native acceptance cases in `qualification/acceptance.csv` (98 targets); these are NOT marked PASS.

## Local-agent actions

Read `AGENTS.md` and `agent/FINISH_AND_VERIFY.md`, then complete real target-desktop verification. Add reproducible evidence under `qualification/runs/` and correct defects. No claim of production readiness is made.
