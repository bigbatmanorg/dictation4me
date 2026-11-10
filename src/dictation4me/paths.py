"""Per-user paths; no dependency imports needed for installation/diagnostics."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


def config_dir() -> Path:
    override = os.environ.get("DICTATION4ME_HOME")
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Dictation4me"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dictation4me"


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    if os.environ.get("DICTATION4ME_HOME") or sys.platform == "win32":
        return config_dir() / "data"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "dictation4me"


def state_dir() -> Path:
    if os.environ.get("DICTATION4ME_HOME") or sys.platform == "win32":
        return config_dir() / "state"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "dictation4me"


def runtime_dir() -> Path:
    if not os.environ.get("DICTATION4ME_HOME") and sys.platform != "win32":
        base = os.environ.get("XDG_RUNTIME_DIR")
        if base:
            return Path(base) / "dictation4me"
    return state_dir() / "runtime"


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.platform != "win32":
        path.chmod(0o700)
    return path


def atomic_write(path: Path, text: str, *, private: bool = True) -> None:
    """Replace a file without a partial-write or world-readable secret window."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".dictation4me-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        if sys.platform != "win32":
            os.chmod(name, 0o600 if private else 0o644)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
