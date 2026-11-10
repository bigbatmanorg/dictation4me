"""Linux clipboard ownership and non-destructive paste-shortcut injection."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import socket

from .. import paths
from ..config import OutputConfig


class DesktopError(RuntimeError):
    pass


def session_type() -> str:
    # A native Wayland session may also export DISPLAY for XWayland.
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


async def run_command(args: list[str], *, data: bytes | None = None,
                      timeout: float = 3, capture: bool = True) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
    except OSError as exc:
        raise DesktopError(f"Cannot start {Path(args[0]).name}; run dictation4me doctor") from exc
    try:
        out, _ = await asyncio.wait_for(process.communicate(data), timeout)
        if process.returncode:
            raise DesktopError(f"{Path(args[0]).name} failed; check your graphical session")
        return out or b""
    except (asyncio.CancelledError, TimeoutError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise


def socket_usable(path: str) -> bool:
    if not path:
        return False
    for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
        with socket.socket(socket.AF_UNIX, kind) as conn:
            conn.settimeout(0.15)
            try:
                conn.connect(path)
                return True
            except (OSError, ValueError):
                pass
    return False


def paste_args(backend: str, shortcut: str) -> list[str]:
    keys = {
        "ctrl+v": [29, 47],
        "ctrl+shift+v": [29, 42, 47],
        "shift+insert": [42, 110],
    }
    if shortcut not in keys:
        raise DesktopError("Unsupported paste shortcut")
    if backend == "xdotool":
        return ["key", "--clearmodifiers", shortcut.replace("insert", "Insert")]
    codes = keys[shortcut]
    return ["key"] + [f"{k}:1" for k in codes] + [f"{k}:0" for k in reversed(codes)]


class LinuxDesktop:
    def __init__(self, config: OutputConfig):
        self.config = config
        self.session = session_type()
        self.daemon: asyncio.subprocess.Process | None = None
        self.socket = ""
        self.prepare_lock = asyncio.Lock()

    @property
    def backend(self) -> str:
        if self.config.backend == "clipboard":
            return "clipboard"
        if self.config.backend != "auto":
            return self.config.backend
        return "xdotool" if self.session == "x11" else "ydotool"

    async def copy(self, text: str) -> None:
        if self.session == "wayland":
            # wl-copy owns the native Wayland clipboard. Never use an XWayland
            # clipboard or a hidden Tk window to imitate a native data-control client.
            args = ["wl-copy", "--type", "text/plain;charset=utf-8"]
        elif self.session == "x11":
            args = ["xclip", "-selection", "clipboard", "-in"]
        else:
            raise DesktopError("No graphical session: transcript cannot be copied")
        # These commands deliberately fork a clipboard owner; never pipe stdout
        # from the child owner, which would keep communicate() waiting indefinitely.
        await run_command(args, data=text.encode("utf-8"), capture=False)
        for _ in range(10):
            if await self.read_clipboard() == text:
                return
            await asyncio.sleep(0.04)
        raise DesktopError("Clipboard did not publish the transcript; it remains in app memory")

    async def read_clipboard(self) -> str:
        args = (["wl-paste", "--no-newline"] if self.session == "wayland"
                else ["xclip", "-selection", "clipboard", "-out"])
        return (await run_command(args)).decode("utf-8", errors="replace")

    async def prepare(self) -> None:
        async with self.prepare_lock:
            await self._prepare()

    async def _prepare(self) -> None:
        if self.backend == "clipboard":
            return
        if self.backend == "xdotool":
            if self.session != "x11":
                raise DesktopError("xdotool cannot paste into native Wayland applications")
            if not shutil.which("xdotool"):
                raise DesktopError("xdotool is missing; run dictation4me setup-system")
            return
        if not shutil.which("ydotool"):
            raise DesktopError("ydotool is missing; clipboard-only mode is still available")
        base = os.environ.get("XDG_RUNTIME_DIR", "")
        managed = str(paths.private_dir(paths.runtime_dir()) / "ydotool.sock")
        candidates = [self.config.ydotool_socket, os.environ.get("YDOTOOL_SOCKET", ""),
                      str(Path(base) / ".ydotool_socket") if base else "",
                      managed, "/tmp/.ydotool_socket"]
        for candidate in dict.fromkeys(candidates):
            if socket_usable(candidate):
                self.socket = candidate
                return
        if not self.config.ydotool_autostart:
            raise DesktopError("No ydotoold socket; enable autostart or configure its socket")
        binary = shutil.which("ydotoold")
        if not binary:
            binary = next((p for p in ("/usr/sbin/ydotoold", "/usr/libexec/ydotoold",
                                      "/usr/local/bin/ydotoold") if os.access(p, os.X_OK)), None)
        if not binary or not os.access("/dev/uinput", os.W_OK):
            raise DesktopError("ydotoold or /dev/uinput permission is missing; run setup-system --enable-uinput")
        if self.daemon is not None and self.daemon.returncode is None:
            self.daemon.terminate()
            await self.daemon.wait()
        try:
            Path(managed).unlink(missing_ok=True)
            self.daemon = await asyncio.create_subprocess_exec(
                binary, "--socket-path", managed, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError as exc:
            raise DesktopError("Could not start private ydotoold") from exc
        for _ in range(60):
            if self.daemon.returncode is not None:
                break
            if socket_usable(managed):
                self.socket = managed
                await asyncio.sleep(0.5)  # compositor discovers the virtual keyboard
                return
            await asyncio.sleep(0.05)
        raise DesktopError("ydotoold did not become ready; check /dev/uinput permissions")

    async def paste(self, shortcut: str) -> None:
        await self.prepare()
        if self.backend == "clipboard":
            raise DesktopError("Automatic paste is disabled")
        args = [self.backend] + paste_args(self.backend, shortcut)
        if self.backend == "ydotool":
            # No shell, no transcript argument, no dynamically interpreted command.
            env = dict(os.environ, YDOTOOL_SOCKET=self.socket)
            process = await asyncio.create_subprocess_exec(
                *args, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except (TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise DesktopError("ydotool could not send paste; transcript stays in the clipboard")
        else:
            await run_command(args)

    async def close(self) -> None:
        if self.daemon is not None and self.daemon.returncode is None:
            self.daemon.terminate()
            try:
                await asyncio.wait_for(self.daemon.wait(), 2)
            except TimeoutError:
                self.daemon.kill()
                await self.daemon.wait()
