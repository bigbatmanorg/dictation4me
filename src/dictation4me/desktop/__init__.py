"""Platform desktop services. No GUI or Core host is needed to use these."""
from __future__ import annotations

import os
import sys

from ..errors import AppError


def session_type() -> str:
    if sys.platform == "win32":
        return "windows"
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


def clipboard():
    if sys.platform == "win32":
        from .windows import WindowsDesktop
        return WindowsDesktop()
    from .linux import LinuxDesktop
    return LinuxDesktop()


def shortcut(value, callback):
    if sys.platform == "win32":
        from .windows import WindowsHotkey
        return WindowsHotkey(value, callback)
    if session_type() == "wayland":
        from .portal import PortalShortcut
        return PortalShortcut(value, callback)
    if session_type() == "x11":
        from .x11 import X11Shortcut
        return X11Shortcut(value, callback)
    raise AppError("NO_DESKTOP", "No graphical session; use the CLI shortcut from your desktop")


def notify(message: str) -> None:
    """Status only. Never pass transcript text or keys to notification services."""
    message = message[:180]
    if sys.platform == "win32":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass
        return
    import shutil
    import subprocess
    if shutil.which("notify-send"):
        argv = ["notify-send", "-a", "Dictation4me", "Dictation4me", message]
    elif shutil.which("gdbus"):
        # Freedesktop Notifications over D-Bus; libnotify-bin is often not installed.
        quoted = message.replace("\\", "\\\\").replace("'", "\\'")
        argv = ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.Notify", "Dictation4me", "0",
                "audio-input-microphone", "Dictation4me", f"'{quoted}'", "[]", "{}", "4000"]
    else:
        return
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:
        pass
