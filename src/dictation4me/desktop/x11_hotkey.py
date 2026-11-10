"""X11 passive key grab. It is intentionally never used for Wayland."""
from __future__ import annotations

import threading
from ..config import parse_hotkey


class X11Hotkey:
    def __init__(self, value, callback):
        self.value, self.callback = value, callback
        self.connection = None
        self.thread = None
        self.stopped = threading.Event()

    def start(self):
        from Xlib import X, XK, display
        mods, key = parse_hotkey(self.value)
        self.connection = display.Display()
        root = self.connection.screen().root
        mask = sum({"ctrl": X.ControlMask, "alt": X.Mod1Mask,
                    "shift": X.ShiftMask, "super": X.Mod4Mask}[m] for m in mods)
        symbol = key.upper() if key.startswith("f") and len(key) > 1 else key
        code = self.connection.keysym_to_keycode(XK.string_to_keysym(symbol))
        if not code:
            self.connection.close()
            self.connection = None
            raise RuntimeError("Shortcut key is not in the active X11 keyboard map")
        self.keycode = code
        errors = []
        for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            root.grab_key(code, mask | extra, True, X.GrabModeAsync, X.GrabModeAsync,
                          onerror=lambda err, req: errors.append(err))
        self.connection.sync()
        if errors:
            self.connection.close()
            self.connection = None
            raise RuntimeError("X11 shortcut is already taken; select another in Settings")
        self.thread = threading.Thread(target=self._run, args=(X.KeyPress, X.KeyRelease), daemon=True,
                                       name="dictation-x11-hotkey")
        self.thread.start()

    def _run(self, key_press, key_release):
        import select
        held = False
        released = False
        release_time = None
        while not self.stopped.is_set():
            if select.select([self.connection.fileno()], [], [], 0.2)[0]:
                while self.connection.pending_events():
                    event = self.connection.next_event()
                    if getattr(event, "detail", None) != self.keycode:
                        continue
                    if event.type == key_release:
                        released = True
                        release_time = event.time
                    elif event.type == key_press:
                        # Traditional X11 autorepeat emits release/press pairs
                        # with identical timestamps; detectable repeat has no
                        # release. Ignore both without disabling system repeat.
                        if not held or (released and event.time != release_time):
                            self.callback()
                        held, released = True, False

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(1)
        if self.connection:
            self.connection.close()
            self.connection = None
