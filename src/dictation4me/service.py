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
        server.serve()
        return 0
    finally:
        maintenance_stop.set()
        if maintenance_thread.is_alive():
            maintenance_thread.join(timeout=3)
        engine.close()
