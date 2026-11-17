"""System tray indicator for Linux desktops (KDE StatusNotifierItem + dbusmenu).

No `from __future__ import annotations` here: dbus-next reads D-Bus signatures
from string annotations at runtime, and postponed evaluation would re-quote them.

Runs inside the resident service on its own asyncio thread. It shows the
dictation state as an icon/tooltip, opens the notebook on left click and has a
small context menu. It never shows transcript text.
"""

import asyncio
import os
import threading

from ..errors import AppError

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

# state -> (icon name, status, tooltip title)
STATES = {
    "IDLE": ("audio-input-microphone", "Active", "Ready to dictate"),
    "CANCELLED": ("audio-input-microphone", "Active", "Ready to dictate"),
    "RECOVERABLE_RESULT": ("audio-input-microphone", "Active", "Ready to dictate"),
    "RECORDING": ("media-record", "NeedsAttention", "Recording…"),
    "FINALIZING": ("view-refresh", "Active", "Transcribing…"),
    "TRANSCRIBING": ("view-refresh", "Active", "Transcribing…"),
    "TRANSFORMING": ("view-refresh", "Active", "Rewriting…"),
    "DELIVERING": ("edit-paste", "Active", "Pasting…"),
    "READY": ("dialog-ok", "Active", "Text ready — press the shortcut"),
    "ERROR": ("dialog-warning", "NeedsAttention", "Needs attention — open Recovery"),
}

MENU_OPEN, MENU_TOGGLE, MENU_QUIT = 1, 2, 4


class Tray:
    def __init__(self, *, on_open, on_toggle, on_quit, shortcut_provider=lambda: ""):
        self.on_open, self.on_toggle, self.on_quit = on_open, on_toggle, on_quit
        self.shortcut_provider = shortcut_provider
        self.state = "IDLE"
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.error = ""
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._item = None
        self._menu = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._thread, name="dictation-tray", daemon=True)
        self.thread.start()
        self._ready.wait(3)

    def _thread(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # the tray is optional; dictation keeps working
            self.error = str(exc) if isinstance(exc, AppError) else "System tray unavailable"
            self._ready.set()

    def update(self, state: str) -> None:
        if self.loop and state != self.state:
            self.loop.call_soon_threadsafe(self._apply, state)

    def close(self) -> None:
        if self.loop and self._stop:
            self.loop.call_soon_threadsafe(self._stop.set)
        if self.thread:
            self.thread.join(timeout=3)

    # ── asyncio side ──
    def _apply(self, state: str) -> None:
        self.state = state
        if self._item:
            self._item.changed()
        if self._menu:
            self._menu.changed()

    async def _run(self) -> None:
        from dbus_next import Message, MessageType
        from dbus_next.aio import MessageBus

        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        bus = await MessageBus().connect()
        self._item = _item_interface(self)
        self._menu = _menu_interface(self)
        bus.export(ITEM_PATH, self._item)
        bus.export(MENU_PATH, self._menu)
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        await bus.request_name(name)
        reply = await bus.call(Message(destination=WATCHER, path="/StatusNotifierWatcher", interface=WATCHER,
                                       member="RegisterStatusNotifierItem", signature="s", body=[name]))
        if reply.message_type == MessageType.ERROR:
            bus.disconnect()
            raise AppError("TRAY_UNAVAILABLE", "No system tray (StatusNotifierWatcher) is running")
        self._ready.set()
        await self._stop.wait()
        bus.disconnect()

    def _menu_items(self) -> list[tuple[int, dict]]:
        recording = self.state == "RECORDING"
        busy = self.state in ("FINALIZING", "TRANSCRIBING", "TRANSFORMING", "DELIVERING")
        toggle = "Stop dictation" if recording else ("Copy dictation" if self.state == "READY" else "Start dictation")
        return [
            (MENU_OPEN, {"label": "Open Dictation4me"}),
            (MENU_TOGGLE, {"label": toggle, "enabled": not busy}),
            (3, {"type": "separator"}),
            (MENU_QUIT, {"label": "Quit background service"}),
        ]

    def _activate(self, item_id: int) -> None:
        action = {MENU_OPEN: self.on_open, MENU_TOGGLE: self.on_toggle, MENU_QUIT: self.on_quit}.get(item_id)
        if action:
            # Never run app work on the D-Bus loop.
            threading.Thread(target=_quietly, args=(action,), daemon=True, name="dictation-tray-action").start()


def _quietly(action) -> None:
    try:
        action()
    except Exception:
        pass


def _item_interface(tray: Tray):
    from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method, signal

    class StatusNotifierItem(ServiceInterface):
        def __init__(self):
            super().__init__("org.kde.StatusNotifierItem")

        def changed(self):
            self.NewIcon()
            self.NewAttentionIcon()
            self.NewStatus(self._status())
            self.NewToolTip()

        def _status(self) -> str:
            return STATES.get(tray.state, STATES["ERROR"])[1]

        @dbus_property(access=PropertyAccess.READ)
        def Category(self) -> "s":
            return "ApplicationStatus"

        @dbus_property(access=PropertyAccess.READ)
        def Id(self) -> "s":
            return "dictation4me"

        @dbus_property(access=PropertyAccess.READ)
        def Title(self) -> "s":
            return "Dictation4me"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return self._status()

        @dbus_property(access=PropertyAccess.READ)
        def WindowId(self) -> "i":
            return 0

        @dbus_property(access=PropertyAccess.READ)
        def IconName(self) -> "s":
            return STATES.get(tray.state, STATES["ERROR"])[0]

        @dbus_property(access=PropertyAccess.READ)
        def AttentionIconName(self) -> "s":
            return STATES.get(tray.state, STATES["ERROR"])[0]

        @dbus_property(access=PropertyAccess.READ)
        def OverlayIconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def ToolTip(self) -> "(sa(iiay)ss)":
            icon, _status, title = STATES.get(tray.state, STATES["ERROR"])
            hint = "Click to open the notebook"
            try:
                shortcut = tray.shortcut_provider()
            except Exception:
                shortcut = ""
            if shortcut:
                hint = f"Shortcut: {shortcut} · {hint}"
            return [icon, [], "Dictation4me — " + title, hint]

        @dbus_property(access=PropertyAccess.READ)
        def ItemIsMenu(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def Menu(self) -> "o":
            return MENU_PATH

        @method()
        def Activate(self, x: "i", y: "i"):
            tray._activate(MENU_OPEN)

        @method()
        def SecondaryActivate(self, x: "i", y: "i"):
            tray._activate(MENU_TOGGLE)

        @method()
        def ContextMenu(self, x: "i", y: "i"):
            pass  # hosts use the exported dbusmenu

        @method()
        def Scroll(self, delta: "i", orientation: "s"):
            pass

        @signal()
        def NewIcon(self):
            return None

        @signal()
        def NewAttentionIcon(self):
            return None

        @signal()
        def NewToolTip(self):
            return None

        @signal()
        def NewStatus(self, status) -> "s":
            return status

    return StatusNotifierItem()


def _menu_interface(tray: Tray):
    from dbus_next import Variant
    from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method, signal

    def props(values: dict) -> dict:
        out = {}
        for key, value in values.items():
            out[key] = Variant("b", value) if isinstance(value, bool) else Variant("s", value)
        return out

    class DBusMenu(ServiceInterface):
        def __init__(self):
            super().__init__("com.canonical.dbusmenu")
            self.revision = 1

        def changed(self):
            self.revision += 1
            self.LayoutUpdated(self.revision, 0)

        @dbus_property(access=PropertyAccess.READ)
        def Version(self) -> "u":
            return 3

        @dbus_property(access=PropertyAccess.READ)
        def TextDirection(self) -> "s":
            return "ltr"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "normal"

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "as":
            return []

        @method()
        def GetLayout(self, parent_id: "i", recursion_depth: "i", property_names: "as") -> "u(ia{sv}av)":
            children = [Variant("(ia{sv}av)", [ident, props(values), []])
                        for ident, values in tray._menu_items()]
            return [self.revision, [0, props({"children-display": "submenu"}), children]]

        @method()
        def GetGroupProperties(self, ids: "ai", property_names: "as") -> "a(ia{sv})":
            return [[ident, props(values)] for ident, values in tray._menu_items() if not ids or ident in ids]

        @method()
        def GetProperty(self, ident: "i", name: "s") -> "v":
            values = dict(tray._menu_items()).get(ident, {})
            return props(values).get(name, Variant("s", ""))

        @method()
        def Event(self, ident: "i", event_id: "s", data: "v", timestamp: "u"):
            if event_id == "clicked":
                tray._activate(ident)

        @method()
        def EventGroup(self, events: "a(isvu)") -> "ai":
            for ident, event_id, _data, _ts in events:
                if event_id == "clicked":
                    tray._activate(ident)
            return []

        @method()
        def AboutToShow(self, ident: "i") -> "b":
            return False

        @method()
        def AboutToShowGroup(self, ids: "ai") -> "aiai":
            return [[], []]

        @signal()
        def LayoutUpdated(self, revision, parent) -> "ui":
            return [revision, parent]

        @signal()
        def ItemsPropertiesUpdated(self) -> "a(ia{sv})a(ias)":
            return [[], []]

    return DBusMenu()
