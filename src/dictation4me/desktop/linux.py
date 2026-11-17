"""Linux Wayland and X11 Unicode clipboard and explicit paste shortcut integration."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

from ..errors import AppError
from . import session_type


class LinuxDesktop:
    def __init__(self):
        self.session = session_type()

    def _command(self, argv: list[str], *, data: bytes | None = None, timeout: float = 4) -> bytes:
        if not shutil.which(argv[0]):
            raise AppError("DESKTOP_DEPENDENCY", f"{argv[0]} is not installed")
        try:
            result = subprocess.run(argv, input=data, stdin=subprocess.DEVNULL if data is None else None,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise AppError("DESKTOP", f"{argv[0]} failed in the graphical session") from exc
        if result.returncode:
            raise AppError("DESKTOP", f"{argv[0]} reported an error")
        return result.stdout

    def copy(self, text: str) -> None:
        if self.session == "wayland":
            self._command(["wl-copy", "--type", "text/plain;charset=utf-8"], data=text.encode("utf-8"))
        elif self.session == "x11":
            self._command(["xclip", "-selection", "clipboard", "-in"], data=text.encode("utf-8"))
        else:
            raise AppError("DESKTOP", "Clipboard requires an interactive desktop session")

    def read(self) -> str:
        if self.session == "wayland":
            return self._command(["wl-paste", "--no-newline"]).decode("utf-8", errors="replace")
        if self.session == "x11":
            return self._command(["xclip", "-selection", "clipboard", "-out"]).decode("utf-8", errors="replace")
        raise AppError("DESKTOP", "No clipboard available in this session")

    @staticmethod
    def _socket_ready(path: str) -> bool:
        if not path:
            return False
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with socket.socket(socket.AF_UNIX, kind) as s:
                s.settimeout(0.2)
                try:
                    s.connect(path)
                    return True
                except (OSError, ValueError):
                    pass
        return False

    def paste(self, shortcut: str, *, unsafe_wayland: bool = False) -> str:
        """Return sent/blocked, never claim target accepted text."""
        key = shortcut.lower()
        valid = {"ctrl+v": [29, 47], "ctrl+shift+v": [29, 42, 47],
                 "shift+insert": [42, 110]}
        if key not in valid:
            return "blocked"
        if self.session == "x11":
            self._command(["xdotool", "key", "--clearmodifiers", key])
            return "sent"
        if self.session != "wayland" or not unsafe_wayland:
            return "blocked"
        # Requires explicit user consent and a separately provisioned, accessible ydotoold.
        # Never start a privileged helper or grant uinput permissions implicitly.
        if not shutil.which("ydotool"):
            return "blocked"
        paths = [os.environ.get("YDOTOOL_SOCKET", ""),
                 str(Path(os.environ.get("XDG_RUNTIME_DIR", "")) / ".ydotool_socket"),
                 "/tmp/.ydotool_socket"]
        sock = next((p for p in paths if self._socket_ready(p)), "")
        if not sock:
            return "blocked"
        codes = valid[key]
        env = dict(os.environ, YDOTOOL_SOCKET=sock)
        command = ["ydotool", "key"] + [f"{k}:1" for k in codes] + [f"{k}:0" for k in reversed(codes)]
        try:
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=4, env=env, check=False)
            return "sent" if result.returncode == 0 else "blocked"
        except (OSError, subprocess.TimeoutExpired):
            return "blocked"

    def foreground_token(self) -> str | None:
        return None  # Wayland deliberately does not expose an original target identity.
