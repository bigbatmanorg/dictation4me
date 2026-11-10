"""Smoke-test the actual uv-installed entry point without audio or a desktop.

Use UV_TOOL_DIR and UV_TOOL_BIN_DIR to select an isolated test installation.
The dependency-free commands are intentional: microphone, portal and native
paste validation must be performed separately on an interactive desktop.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    bindir = Path(subprocess.check_output(["uv", "tool", "dir", "--bin"], text=True).strip())
    entry = bindir / ("dictation4me.exe" if sys.platform == "win32" else "dictation4me")
    if not entry.is_file():
        raise RuntimeError(f"Installed entry point missing: {entry}")
    with tempfile.TemporaryDirectory(prefix="dictation4me-installed-") as tmp:
        env = dict(os.environ, DICTATION4ME_HOME=tmp)
        env.pop("PYTHONPATH", None)
        for command in (["--version"], ["--help"], ["init", "--no-gui", "--no-autostart"]):
            subprocess.run([str(entry), *command], check=True, env=env, cwd=tmp, timeout=40)
        output = subprocess.check_output([str(entry), "paths"], text=True, env=env, cwd=tmp, timeout=10)
        config = Path(json.loads(output)["config"])
        assert config.is_file() and config.is_relative_to(tmp)
        subprocess.run([str(entry), "uninstall", "--purge"], check=True, env=env, cwd=tmp, timeout=15)
        assert not config.exists()
    print("Installed-wheel entry point, isolated onboarding and removal: PASS")


if __name__ == "__main__":
    main()
