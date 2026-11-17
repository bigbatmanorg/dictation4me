"""Optional X11-only passive key grab; never used on native Wayland."""
from __future__ import annotations

import select
import threading

from ..errors import AppError


class X11Shortcut:
    def __init__(self, value, callback):
        self.value, self.callback = value, callback
        self.connection = None
        self.thread = None
        self.stopped = threading.Event()

    def start(self):
        try:
            from Xlib import XK, X, display
        except ImportError as exc:
            raise AppError("HOTKEY", "python-xlib is required for X11 shortcuts") from exc
        parts = self.value.lower().split("+")
        mods, key = parts[:-1], parts[-1]
        mask = sum({"ctrl": X.ControlMask, "alt": X.Mod1Mask,
                    "shift": X.ShiftMask, "super": X.Mod4Mask, "meta": X.Mod4Mask}[m] for m in mods)
        symbol = key.upper() if key.startswith("f") and key[1:].isdigit() else key
        self.connection = display.Display()
        code = self.connection.keysym_to_keycode(XK.string_to_keysym(symbol))
        if not code:
            self.connection.close()
            raise AppError("HOTKEY", "Shortcut key is absent from X11 keyboard map")
        self.code = code
        root = self.connection.screen().root
        errors = []
        for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            root.grab_key(code, mask | extra, True, X.GrabModeAsync, X.GrabModeAsync,
                          onerror=lambda error, request: errors.append(error))
        self.connection.sync()
        if errors:
            self.connection.close()
            raise AppError("HOTKEY", "Shortcut is already registered in X11")
        self.thread = threading.Thread(target=self._run, daemon=True, name="dictation-x11-hotkey")
        self.thread.start()

    def _run(self):
        from Xlib import X
        pressed = False
        release_ts = None
        released = False
        while not self.stopped.is_set():
            try:
                ready = select.select([self.connection.fileno()], [], [], .15)[0]
                if not ready:
                    continue
                while self.connection.pending_events():
                    event = self.connection.next_event()
                    if getattr(event, "detail", None) != self.code:
                        continue
                    if event.type == X.KeyRelease:
                        released, release_ts = True, event.time
                    elif event.type == X.KeyPress:
                        if not pressed or (released and event.time != release_ts):
                            self.callback()
                        pressed, released = True, False
            except (OSError, ValueError):
                break

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.connection:
            self.connection.close()
