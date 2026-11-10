"""Pure-Python StatusNotifierItem, DBusMenu and GlobalShortcuts portal.

No GTK/PyGObject dependency is required inside the uv tool environment.
"""
import asyncio
import contextlib
import secrets

from dbus_next import Variant, Message, MessageType
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, method, dbus_property, signal

from ..config import parse_hotkey
from .. import APP_ID

READ = PropertyAccess.READ
PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"


class Menu(ServiceInterface):
    def __init__(self, callback):
        super().__init__("com.canonical.dbusmenu")
        self.callback = callback
        self.revision = 1
        self.state = {"state": "idle", "can_retry": False, "has_last_text": False}

    def items(self):
        busy = self.state["state"] not in ("idle", "recording")
        return {
            1: (f"Dictation4me: {self.state['state']}", False, ""),
            2: ("Stop recording" if self.state["state"] == "recording" else "Start recording", not busy, "toggle"),
            3: ("Cancel", self.state["state"] != "idle", "cancel"),
            4: ("Retry transcription", self.state.get("can_retry", False), "retry"),
            5: ("Copy last transcript", self.state.get("has_last_text", False) and not busy, "copy-last"),
            6: ("Settings…", True, "settings"),
            7: ("Quit", True, "shutdown"),
        }

    def props(self, ident):
        if ident == 0:
            return {"children-display": Variant("s", "submenu")}
        label, enabled, _ = self.items()[ident]
        return {"label": Variant("s", label), "enabled": Variant("b", bool(enabled)),
                "visible": Variant("b", True)}

    def node(self, ident, depth, names):
        props = self.props(ident)
        if names:
            props = {key: value for key, value in props.items() if key in names}
        children = []
        if ident == 0 and depth != 0:
            children = [Variant("(ia{sv}av)", self.node(i, depth - 1, names)) for i in self.items()]
        return [ident, props, children]

    @method()
    def GetLayout(self, parentId: 'i', recursionDepth: 'i', propertyNames: 'as') -> 'u(ia{sv}av)':
        return [self.revision, self.node(parentId, recursionDepth, propertyNames)]

    @method()
    def GetGroupProperties(self, ids: 'ai', propertyNames: 'as') -> 'a(ia{sv})':
        return [[i, self.node(i, 0, propertyNames)[1]] for i in (ids or list(self.items()))]

    @method()
    def GetProperty(self, id: 'i', name: 's') -> 'v':
        return self.props(id).get(name, Variant("s", ""))

    @method()
    def Event(self, id: 'i', eventId: 's', data: 'v', timestamp: 'u'):
        item = self.items().get(id)
        if eventId == "clicked" and item and item[1]:
            self.callback(item[2])

    @method()
    def EventGroup(self, events: 'a(isvu)') -> 'ai':
        errors = []
        for ident, event, data, timestamp in events:
            if ident not in self.items():
                errors.append(ident)
            else:
                self.Event(ident, event, data, timestamp)
        return errors

    @method()
    def AboutToShow(self, id: 'i') -> 'b':
        return False

    @method()
    def AboutToShowGroup(self, ids: 'ai') -> 'aiai':
        return [[], []]

    @dbus_property(access=READ)
    def Version(self) -> 'u':
        return 3

    @dbus_property(access=READ)
    def TextDirection(self) -> 's':
        return "ltr"

    @dbus_property(access=READ)
    def Status(self) -> 's':
        return "normal"

    @dbus_property(access=READ)
    def IconThemePath(self) -> 'as':
        return []

    @signal()
    def LayoutUpdated(self, revision: 'u', parent: 'i') -> 'ui':
        return [revision, parent]

    def update(self, state):
        changed = any(state.get(k) != self.state.get(k) for k in ("state", "can_retry", "has_last_text"))
        self.state = state
        if changed:
            self.revision += 1
            self.LayoutUpdated(self.revision, 0)


class Tray(ServiceInterface):
    def __init__(self, callback):
        super().__init__("org.kde.StatusNotifierItem")
        self.callback = callback
        self.detail = "Ready"
        self.current_icon = "audio-input-microphone"

    @dbus_property(access=READ)
    def Category(self) -> 's':
        return "ApplicationStatus"

    @dbus_property(access=READ)
    def Id(self) -> 's':
        return "dictation4me"

    @dbus_property(access=READ)
    def Title(self) -> 's':
        return "Dictation4me"

    @dbus_property(access=READ)
    def Status(self) -> 's':
        return "Active"

    @dbus_property(access=READ)
    def WindowId(self) -> 'u':
        return 0

    @dbus_property(access=READ)
    def IconName(self) -> 's':
        return self.current_icon

    @dbus_property(access=READ)
    def IconPixmap(self) -> 'a(iiay)':
        return []

    @dbus_property(access=READ)
    def AttentionIconName(self) -> 's':
        return "dialog-warning"

    @dbus_property(access=READ)
    def AttentionIconPixmap(self) -> 'a(iiay)':
        return []

    @dbus_property(access=READ)
    def OverlayIconName(self) -> 's':
        return ""

    @dbus_property(access=READ)
    def OverlayIconPixmap(self) -> 'a(iiay)':
        return []

    @dbus_property(access=READ)
    def ToolTip(self) -> '(sa(iiay)ss)':
        return [self.current_icon, [], "Dictation4me", self.detail]

    @dbus_property(access=READ)
    def ItemIsMenu(self) -> 'b':
        return False

    @dbus_property(access=READ)
    def Menu(self) -> 'o':
        return "/Menu"

    @method()
    def Activate(self, x: 'i', y: 'i'):
        self.callback("toggle")

    @method()
    def SecondaryActivate(self, x: 'i', y: 'i'):
        self.callback("settings")

    @method()
    def ContextMenu(self, x: 'i', y: 'i'):
        pass  # The host renders the exported DBusMenu.

    @method()
    def Scroll(self, delta: 'i', orientation: 's'):
        pass

    @signal()
    def NewIcon(self):
        pass

    @signal()
    def NewToolTip(self):
        pass

    def update(self, state):
        self.detail = state["message"]
        if state["state"] == "recording":
            self.detail += f" ({state['seconds']}s)"
        icon = {"recording": "media-record", "transcribing": "view-refresh",
                "inserting": "edit-paste"}.get(state["state"], "audio-input-microphone")
        if icon != self.current_icon:
            self.current_icon = icon
            self.NewIcon()
        self.NewToolTip()


class LinuxBus:
    def __init__(self, callback):
        self.callback = callback
        self.bus = None
        self.tray = Tray(callback)
        self.menu = Menu(callback)
        self.session = None
        self.tray_ready = False
        self.with_tray = True
        self.shortcut_description = "Not registered"

    async def start(self, with_tray=True):
        self.with_tray = with_tray
        self.bus = await MessageBus().connect()
        if with_tray:
            self.bus.export("/StatusNotifierItem", self.tray)
            self.bus.export("/Menu", self.menu)
        self.bus.add_message_handler(self._signal)
        await self._match("type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.kde.StatusNotifierWatcher'")
        await self.register_tray()

    async def _match(self, rule):
        await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                    interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule]))

    async def register_tray(self):
        if not self.with_tray:
            return
        reply = await self.bus.call(Message(destination="org.kde.StatusNotifierWatcher",
                                             path="/StatusNotifierWatcher", interface="org.kde.StatusNotifierWatcher",
                                             member="RegisterStatusNotifierItem", signature="s", body=[self.bus.unique_name]))
        self.tray_ready = reply.message_type != MessageType.ERROR

    def _signal(self, message):
        if message.message_type != MessageType.SIGNAL:
            return
        if message.interface == "org.freedesktop.DBus" and message.member == "NameOwnerChanged":
            if message.body[0] == "org.kde.StatusNotifierWatcher" and message.body[2]:
                asyncio.create_task(self.register_tray())
        if message.interface == SHORTCUTS and message.member == "Activated":
            if message.body[:2] == [self.session, "dictate"]:
                self.callback("hotkey")

    async def request(self, member, signature, body, options):
        token = "d" + secrets.token_hex(8)
        options["handle_token"] = Variant("s", token)
        messages = {}
        event = asyncio.Event()
        rule = "type='signal',interface='org.freedesktop.portal.Request',member='Response'"

        def receive(msg):
            if msg.message_type == MessageType.SIGNAL and msg.interface == "org.freedesktop.portal.Request" and msg.member == "Response":
                messages[msg.path] = msg.body
                event.set()

        self.bus.add_message_handler(receive)
        await self._match(rule)
        handle = None
        try:
            reply = await self.bus.call(Message(destination=PORTAL, path=PORTAL_PATH, interface=SHORTCUTS,
                                                member=member, signature=signature, body=body + [options]))
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError("GlobalShortcuts portal unavailable; use a desktop command shortcut")
            handle = reply.body[0]
            async with asyncio.timeout(90):
                while handle not in messages:
                    event.clear()
                    await event.wait()
            code, results = messages[handle]
            if code != 0:
                raise RuntimeError("Shortcut permission was declined; assign a command shortcut instead")
            return results
        except BaseException:
            if handle:
                with contextlib.suppress(Exception):
                    await self.bus.call(Message(destination=PORTAL, path=handle,
                                                interface="org.freedesktop.portal.Request", member="Close"))
            raise
        finally:
            self.bus.remove_message_handler(receive)
            await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                        interface="org.freedesktop.DBus", member="RemoveMatch", signature="s", body=[rule]))

    async def register_shortcut(self, hotkey):
        # A plain Python/uv process needs an application identity. Older portals
        # do not implement Registry; retain the command-shortcut fallback.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.bus.call(Message(
                destination=PORTAL, path=PORTAL_PATH,
                interface="org.freedesktop.host.portal.Registry", member="Register",
                signature="sa{sv}", body=[APP_ID, {}])), 5)
        await self._match(f"type='signal',interface='{SHORTCUTS}'")
        result = await self.request("CreateSession", "a{sv}", [],
                                    {"session_handle_token": Variant("s", "s" + secrets.token_hex(8))})
        self.session = result["session_handle"].value
        mods, key = parse_hotkey(hotkey)
        trigger = "+".join([name for token, name in (("ctrl", "CTRL"), ("alt", "ALT"),
                                                       ("shift", "SHIFT"), ("super", "LOGO")) if token in mods]
                           + [key.upper()])
        result = await self.request("BindShortcuts", "oa(sa{sv})sa{sv}",
                                   [self.session, [["dictate", {"description": Variant("s", "Start / stop dictation"),
                                                                 "preferred_trigger": Variant("s", trigger)}]], ""], {})
        shortcuts = result.get("shortcuts", Variant("a(sa{sv})", [])).value
        if not any(item[0] == "dictate" for item in shortcuts):
            raise RuntimeError("Portal did not bind the dictation shortcut")
        props = next(item[1] for item in shortcuts if item[0] == "dictate")
        self.shortcut_description = props.get("trigger_description", Variant("s", hotkey)).value

    def update(self, state):
        if self.with_tray:
            self.tray.update(state)
            self.menu.update(state)

    async def notify(self, message):
        if not self.bus:
            return
        # No transcript text is sent to the notification service.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.bus.call(Message(
                destination="org.freedesktop.Notifications", path="/org/freedesktop/Notifications",
                interface="org.freedesktop.Notifications", member="Notify", signature="susssasa{sv}i",
                body=["Dictation4me", 0, "audio-input-microphone", "Dictation4me", message, [], {}, 5000])), 2)

    async def close(self):
        if self.bus:
            if self.session:
                with contextlib.suppress(Exception):
                    await self.bus.call(Message(destination=PORTAL, path=self.session,
                                                interface="org.freedesktop.portal.Session", member="Close"))
            self.bus.disconnect()
