"""Resident headless service. Does not create NativeHTMLKit windows."""
from __future__ import annotations

import os
import threading

from .engine import Engine
from .ipc import RpcServer
from .paths import Paths


def run() -> int:
    paths = Paths.discover().init()
    engine = Engine(paths)
    server = None
    tray = None
    maintenance_stop = threading.Event()

    def maintenance() -> None:
        while not maintenance_stop.is_set():
            try:
                engine.housekeep()
            except Exception:
                # Startup/periodic maintenance must never bring down dictation.
                pass
            if maintenance_stop.wait(600):
                return

    maintenance_thread = threading.Thread(target=maintenance, daemon=True,
                                          name="dictation-maintenance")
    try:
        def handler(action, params):
            result = engine.handle(action, params)
            if action == "service.stop":
                # Send a successful response before beginning RPC shutdown.
                threading.Timer(.1, server.stop).start()
            return result
        server = RpcServer(paths, handler)
        maintenance_thread.start()
        if not os.environ.get("DICTATION4ME_NO_HOTKEY"):
            # Tests/automation set this so they never touch the desktop's real bindings.
            engine.start_hotkey()
        tray = _start_tray(engine, server)
        server.serve()
        return 0
    finally:
        if tray:
            tray.close()
        maintenance_stop.set()
        if maintenance_thread.is_alive():
            maintenance_thread.join(timeout=3)
        engine.close()


def _open_notebook() -> None:
    """Launch the notebook; NativeHTMLKit forwards a second launch to the open window."""
    import subprocess
    import sys
    subprocess.Popen([sys.executable, "-m", "dictation4me", "gui"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, start_new_session=True)


def _start_tray(engine: Engine, server):
    import sys

    from . import desktop
    if (not sys.platform.startswith("linux") or os.environ.get("DICTATION4ME_NO_TRAY")
            or desktop.session_type() not in ("wayland", "x11")):
        return None
    try:
        from .desktop.tray import Tray
        tray = Tray(on_open=_open_notebook, on_toggle=engine.hotkey, on_quit=server.stop,
                    shortcut_provider=engine.active_shortcut)
        tray.start()
        engine.add_listener(tray.update)
        return tray
    except Exception:
        return None  # the tray is optional; dictation keeps working
