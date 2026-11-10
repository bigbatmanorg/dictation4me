"""Resident asyncio service. TTK settings are a separate, on-demand process."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import subprocess
import sys
import time

from . import paths
from .config import load
from .controller import Controller
from .ipc import Server, request
from .onboarding import launcher_command

log = logging.getLogger("dictation4me")


def spawn_settings():
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(launcher_command("settings", graphical=True), **options)


class Service:
    def __init__(self, *, no_tray=False, no_hotkey=False, desktop=None, recorder=None, transcriber=None):
        self.config = load()
        if desktop is None:
            if sys.platform == "win32":
                from .desktop.windows import WindowsDesktop
                desktop = WindowsDesktop(self.config.output)
            elif sys.platform == "linux":
                from .desktop.linux import LinuxDesktop
                desktop = LinuxDesktop(self.config.output)
            else:
                raise RuntimeError("This release supports Linux and Windows only")
        options = {"changed": self.changed}
        if recorder is not None:
            options["recorder"] = recorder
        if transcriber is not None:
            options["transcriber"] = transcriber
        self.controller = Controller(self.config, desktop, **options)
        self.server = Server(self.handle)
        self.shutdown = asyncio.Event()
        self.no_tray, self.no_hotkey = no_tray, no_hotkey
        self.bus = None
        self.tray = None
        self.hotkey = None
        self.tasks: set[asyncio.Task] = set()
        self.settings_process = None
        self.capabilities = {}
        self.last_notice = ""

    def task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def dispatch(self, action):
        self.task(self.handle(action))

    def changed(self, status):
        # State logs deliberately omit messages: exceptions from third-party
        # libraries may contain sensitive input. The UI can show actionable text.
        log.info("state=%s", status["state"])
        if self.bus:
            self.bus.update(status)
        if self.tray:
            self.tray.update(status)
        if status["state"] == "idle" and status["message"] != self.last_notice:
            self.last_notice = status["message"]
            if self.bus:
                self.task(self.bus.notify(status["message"]))
            if self.tray:
                self.tray.notify(status["message"])

    async def handle(self, action):
        if action == "status":
            return {"ok": True, **self.controller.status(), "capabilities": self.capabilities}
        if action == "shutdown":
            asyncio.get_running_loop().call_later(0.1, self.shutdown.set)
            return {"ok": True, "message": "Service stopping"}
        if action == "settings":
            if self.settings_process is None or self.settings_process.poll() is not None:
                self.settings_process = spawn_settings()
            return {"ok": True}
        if action == "hotkey":
            await self.controller.hotkey()
            return {"ok": True}
        if action == "reload":
            if self.controller.state != "idle":
                return {"ok": False, "error": "Finish or cancel recording before applying settings"}
            try:
                cfg = load()
                restart = cfg.desktop.hotkey != self.config.desktop.hotkey or cfg.desktop.hotkey_backend != self.config.desktop.hotkey_backend
                self.config = cfg
                self.controller.config = cfg
                self.controller.desktop.config = cfg.output
                return {"ok": True, "restart_required": restart,
                        "message": "Restart service to apply shortcut changes" if restart else "Settings applied"}
            except Exception:
                return {"ok": False, "error": "Invalid configuration; previous settings remain active"}
        return await self.controller.command(action)

    async def _prepare_output(self):
        try:
            await self.controller.desktop.prepare()
            self.capabilities["paste backend"] = {"ready": True, "detail": "Ready (target acceptance not verified)"}
        except Exception as exc:
            self.capabilities["paste backend"] = {"ready": False, "detail": str(exc)}

    async def _desktop_setup(self):
        loop = asyncio.get_running_loop()
        def callback(action):
            loop.call_soon_threadsafe(self.dispatch, action)
        if sys.platform == "linux":
            if not self.no_tray or (not self.no_hotkey and self.config.desktop.hotkey_backend in ("auto", "portal")):
                try:
                    from .desktop.linux_bus import LinuxBus
                    self.bus = LinuxBus(callback)
                    await asyncio.wait_for(self.bus.start(with_tray=not self.no_tray), 5)
                    self.capabilities["tray"] = {"ready": self.bus.tray_ready,
                                                 "detail": "StatusNotifierItem" if self.bus.tray_ready else "No tray host; CLI and settings remain available"}
                except Exception:
                    self.capabilities["tray"] = {"ready": False, "detail": "Session D-Bus or dbus-next unavailable"}
                    if self.bus:
                        await self.bus.close()
                    self.bus = None
            if not self.no_hotkey:
                from .desktop.linux import session_type
                chosen = self.config.desktop.hotkey_backend
                if chosen == "external":
                    self.capabilities["shortcut"] = {"ready": False, "detail": "External mode: assign dictation4me toggle in desktop shortcuts"}
                elif chosen == "x11" or (chosen == "auto" and session_type() == "x11"):
                    try:
                        if session_type() != "x11":
                            raise RuntimeError("X11 shortcut backend cannot handle Wayland")
                        from .desktop.x11_hotkey import X11Hotkey
                        self.hotkey = X11Hotkey(self.config.desktop.hotkey, lambda: callback("hotkey"))
                        await asyncio.to_thread(self.hotkey.start)
                        self.capabilities["shortcut"] = {"ready": True, "detail": self.config.desktop.hotkey}
                    except Exception as exc:
                        self.capabilities["shortcut"] = {"ready": False, "detail": str(exc)}
                elif chosen in ("auto", "portal") and self.bus:
                    self.capabilities["shortcut"] = {"ready": False, "detail": "Waiting for portal shortcut authorization"}
                    try:
                        await self.bus.register_shortcut(self.config.desktop.hotkey)
                        self.capabilities["shortcut"] = {"ready": True, "detail": self.bus.shortcut_description}
                    except Exception as exc:
                        self.capabilities["shortcut"] = {"ready": False, "detail": str(exc) or "Portal permission request timed out"}
                else:
                    self.capabilities["shortcut"] = {"ready": False, "detail": "Assign the command dictation4me toggle in desktop shortcut settings"}
        elif sys.platform == "win32":
            if not self.no_tray:
                try:
                    from .desktop.windows_tray import WindowsTray
                    self.tray = WindowsTray(callback)
                    self.tray.start()
                    self.capabilities["tray"] = {"ready": True, "detail": "Windows notification area"}
                except Exception as exc:
                    self.capabilities["tray"] = {"ready": False, "detail": str(exc)}
            if not self.no_hotkey and self.config.desktop.hotkey_backend != "external":
                try:
                    from .desktop.windows import WindowsHotkey
                    self.hotkey = WindowsHotkey(self.config.desktop.hotkey, lambda: callback("hotkey"))
                    await asyncio.to_thread(self.hotkey.start)
                    self.capabilities["shortcut"] = {"ready": True, "detail": self.config.desktop.hotkey}
                except Exception as exc:
                    self.capabilities["shortcut"] = {"ready": False, "detail": str(exc)}

    async def run(self):
        await self.server.start()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(signum, self.shutdown.set)
        self.task(self._desktop_setup())
        self.task(self._prepare_output())
        try:
            while not self.shutdown.is_set():
                try:
                    await asyncio.wait_for(self.shutdown.wait(), 1)
                except TimeoutError:
                    state = self.controller.status()
                    if self.bus:
                        self.bus.update(state)
                    if self.tray and state["state"] == "recording":
                        self.tray.update(state)
        finally:
            for task in list(self.tasks):
                task.cancel()
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            if self.hotkey:
                await asyncio.to_thread(self.hotkey.close)
            if self.tray:
                await asyncio.to_thread(self.tray.close)
            # Remove UI references before controller.close emits a final state.
            bus, self.bus = self.bus, None
            self.tray = None
            if bus:
                await bus.close()
            await self.controller.close()
            await self.server.close()


def launch(*, timeout=8) -> dict:
    try:
        return request("status", timeout=0.5)
    except ConnectionError:
        pass
    load()  # fail before spawning a process with invalid configuration
    directory = paths.private_dir(paths.state_dir())
    logfile = directory / "service.log"
    if logfile.exists() and logfile.stat().st_size > 1024 * 1024:
        logfile.replace(directory / "service.previous.log")
    flags = {}
    if sys.platform == "win32":
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        flags["start_new_session"] = True
    with logfile.open("ab") as log_file:
        if sys.platform != "win32":
            logfile.chmod(0o600)
        subprocess.Popen(launcher_command("run", graphical=True), stdin=subprocess.DEVNULL,
                                 stdout=log_file, stderr=log_file, **flags)
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            return request("status", timeout=0.3)
        except ConnectionError:
            # A concurrent launcher may have won the instance lock. Wait for its socket too.
            time.sleep(0.1)
    raise RuntimeError(f"Service did not become ready. See {logfile}; run dictation4me run for visible diagnostics")
