"""Read-only capability checks; never records or sends audio automatically."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys

from . import __version__, paths
from .config import ConfigError, load


def diagnose(*, audio=True) -> list[dict]:
    rows = []

    def add(name, level, detail):
        rows.append({"check": name, "level": level, "detail": detail})

    add("version", "ok", f"dictation4me {__version__}; Python {sys.version.split()[0]}")
    try:
        cfg = load()
        add("configuration", "ok", str(paths.config_file()))
        add("endpoint", "ok", cfg.service.endpoint() + " (not contacted)")
        add("credential", "ok" if cfg.service.key() else "warn",
            "Present (not displayed)" if cfg.service.key() else "No key; acceptable only for an unauthenticated endpoint")
    except ConfigError as exc:
        add("configuration", "fail", str(exc))
        cfg = None
    try:
        import tkinter
        add("Tk", "ok", f"Tcl/Tk {tkinter.TkVersion}; display not opened")
    except ImportError:
        add("Tk", "fail", "Missing for this Python. On Debian use python3-tk and uv tool install --python /usr/bin/python3 .")
    if sys.platform == "linux":
        from .desktop.linux import session_type, socket_usable
        session = session_type()
        add("session", "fail" if session == "headless" else "ok", session)
        commands = ("wl-copy", "wl-paste") if session == "wayland" else ("xclip",)
        for command in commands:
            add(command, "ok" if shutil.which(command) else "fail",
                shutil.which(command) or "Missing; run dictation4me setup-system")
        add("session D-Bus", "ok" if os.environ.get("DBUS_SESSION_BUS_ADDRESS") else "warn",
            "Available" if os.environ.get("DBUS_SESSION_BUS_ADDRESS") else "Not detected; launch from the desktop session")
        add("D-Bus Python", "ok" if importlib.util.find_spec("dbus_next") else "fail",
            "Installed" if importlib.util.find_spec("dbus_next") else "Missing Python dependency; reinstall the uv tool")
        if cfg and cfg.output.auto_paste and cfg.output.backend != "clipboard":
            backend = cfg.output.backend if cfg.output.backend != "auto" else ("xdotool" if session == "x11" else "ydotool")
            add("paste command", "ok" if shutil.which(backend) else "warn", shutil.which(backend) or f"Missing {backend}; clipboard-only fallback")
            if backend == "ydotool":
                add("uinput", "ok" if os.access("/dev/uinput", os.W_OK) else "warn",
                    "Writable" if os.access("/dev/uinput", os.W_OK) else "No direct access; external ydotoold may still work. See setup-system --enable-uinput")
                configured = cfg.output.ydotool_socket or os.environ.get("YDOTOOL_SOCKET", "")
                if configured:
                    add("ydotool socket", "ok" if socket_usable(configured) else "warn", configured)
    elif sys.platform == "win32":
        add("platform", "ok", "Windows native clipboard / RegisterHotKey / SendInput")
        add("desktop access", "warn", "Use an interactive desktop. Elevated targets may reject input.")
    else:
        add("platform", "fail", "Only Linux and Windows are supported")
    if audio:
        try:
            from .audio import list_devices, resolve_device
            devices = list_devices()
            add("microphones", "ok" if devices else "fail", f"{len(devices)} input device(s)")
            if devices and cfg:
                resolve_device(cfg.audio.device, devices)
                add("selected microphone", "ok", cfg.audio.device)
        except Exception as exc:
            add("microphones", "fail", str(exc))
    try:
        from .ipc import request
        status = request("status", timeout=1)
        add("service", "ok", f"Running: {status.get('state')}")
        for key, value in status.get("capabilities", {}).items():
            add(key, "ok" if value.get("ready") else "warn", value.get("detail", ""))
    except ConnectionError:
        add("service", "warn", "Not running; use dictation4me launch")
    return rows
