"""Read-only diagnostics; never print environment values, tokens or note content."""
from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import sqlite3
import sys
from contextlib import closing

from . import __version__
from .config import load
from .desktop import session_type
from .errors import HINTS
from .paths import Paths


def doctor(paths: Paths) -> dict:
    state = session_type()
    info = {"dictation4me": __version__, "python": sys.version.split()[0],
            "platform": platform.platform(), "session": state,
            "service_reachable": False,
            "nativehtmlkit": None,
            "audio_dependency": False, "audio_input_count": None,
            "clipboard_copy_available": None, "paste_backend_ready": False,
            "keyring_available": False, "sqlite_fts5": False, "configuration": "missing",
            "issues": []}
    try:
        from .ipc import invoke
        state_reply = invoke(paths, "status", timeout=.6)
        info["service_reachable"] = True
        info["service_state"] = state_reply["state"]
        info["hotkey"] = state_reply["hotkey"]
    except Exception:
        info["issues"].append("Background service is not running. Fix: " + HINTS["SERVICE_UNAVAILABLE"])
    for package in ("nativehtmlkit",):
        try:
            info[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            info["issues"].append(f"Python package '{package}' is missing. Fix: " + HINTS["GUI_UNAVAILABLE"])
    try:
        from .audio import devices
        info["audio_input_count"] = len(devices())
        info["audio_dependency"] = True
    except Exception as exc:
        code = getattr(exc, "code", "AUDIO_UNAVAILABLE")
        try:
            import sounddevice  # noqa: F401
        except (ImportError, OSError):
            code = "AUDIO_UNAVAILABLE"
        info["issues"].append("Microphone is not usable. Fix: " + HINTS.get(code, HINTS["AUDIO_OPEN_FAILED"]))
    try:
        import keyring
        info["keyring_available"] = bool(keyring.get_keyring().priority > 0)
    except Exception:
        info["issues"].append("No OS keyring. Fix: " + HINTS["KEYRING"])
    with closing(sqlite3.connect(":memory:")) as db:
        try:
            db.execute("CREATE VIRTUAL TABLE test_fts USING fts5(value)")
            info["sqlite_fts5"] = True
        except sqlite3.OperationalError:
            info["issues"].append("SQLite FTS5 unavailable")
    if state == "wayland":
        info["clipboard_copy_available"] = shutil.which("wl-copy") is not None
        info["clipboard_read_available"] = shutil.which("wl-paste") is not None
        info["paste_backend_ready"] = shutil.which("ydotool") is not None and os.access("/dev/uinput", os.W_OK)
        if not info["clipboard_copy_available"] or not info["clipboard_read_available"]:
            info["issues"].append("Clipboard tools missing. Fix: sudo apt install wl-clipboard")
        try:
            import shutil as _sh
            from pathlib import Path as _P
            apps = _P.home() / ".local/share/applications/org.bigbatmanorg.dictation4me.desktop"
            if not apps.exists() and not _sh.which("dictation4me-gui"):
                info["issues"].append("Desktop entry missing (needed for the KDE shortcut and auto-paste). "
                                      "Fix: dictation4me init")
        except OSError:
            pass
    elif state == "x11":
        info["clipboard_copy_available"] = shutil.which("xclip") is not None
        info["paste_backend_ready"] = shutil.which("xdotool") is not None
    elif state == "windows":
        info["clipboard_copy_available"] = True
        info["paste_backend_ready"] = True
    else:
        info["issues"].append("No interactive desktop session detected")
    try:
        cfg = load(paths)
        info["configuration"] = "valid" if paths.config_file.exists() else "defaults-only"
        info["stt_model_configured"] = bool(cfg["stt"]["model"])
        info["ai_model_configured"] = bool(cfg["ai"]["model"])
        if not cfg["stt"]["model"]:
            info["issues"].append("No speech-to-text model. Fix: " + HINTS["MODEL_NOT_CONFIGURED"])
    except Exception:
        info["configuration"] = "invalid"
        info["issues"].append(f"Settings file is invalid. Fix: correct or delete {paths.config_file} "
                              "(defaults are recreated)")
    return info
