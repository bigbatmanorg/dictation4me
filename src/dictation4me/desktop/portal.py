"""Freedesktop GlobalShortcuts portal on KDE Wayland; command-shortcut fallback supported."""
from __future__ import annotations

import asyncio
import contextlib
import secrets
import threading

from .. import APP_ID
from ..errors import AppError

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"


class PortalShortcut:
    def __init__(self, value: str, callback):
        self.value, self.callback = value, callback
        self.loop = None
        self.bus = None
        self.session = None
        self.error = None
        self.ready = threading.Event()
        self.thread = None
        self._stop = None
        self.description = None  # set from the portal reply once bound
        self.registered = True

    def start(self):
        self.thread = threading.Thread(target=self._thread, name="dictation-wayland-portal", daemon=True)
        self.thread.start()
        # Consent UI may take longer; the service remains healthy and CLI fallback works.
        self.ready.wait(1.5)

    def _thread(self):
        try:
            asyncio.run(self._run())
        except AppError as exc:
            self.error = str(exc)
            self.ready.set()
        except Exception:
            self.error = ("The desktop shortcut portal is unavailable — bind 'dictation4me toggle' as a custom "
                          "shortcut in System Settings › Keyboard › Shortcuts")
            self.ready.set()

    async def _run(self):
        from dbus_next import Message, MessageType, Variant
        from dbus_next.aio import MessageBus
        self.bus = await MessageBus().connect()
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()

        def signal(message):
            if (message.message_type == MessageType.SIGNAL and message.interface == SHORTCUTS
                and message.member == "Activated" and self.session
                and len(message.body) >= 2 and message.body[0] == self.session
                and message.body[1] == "dictate"):
                self.callback()

        self.bus.add_message_handler(signal)
        await self._match("type='signal',interface='org.freedesktop.portal.GlobalShortcuts'")

        async def request(member, signature, args, options):
            token = "r" + secrets.token_hex(8)
            options = dict(options)
            options["handle_token"] = Variant("s", token)
            responses = {}
            signaled = asyncio.Event()
            response_rule = "type='signal',interface='org.freedesktop.portal.Request',member='Response'"

            def receive(msg):
                if msg.message_type == MessageType.SIGNAL and msg.interface == "org.freedesktop.portal.Request" and msg.member == "Response":
                    responses[msg.path] = msg.body
                    signaled.set()

            self.bus.add_message_handler(receive)
            await self._match(response_rule)
            handle = None
            try:
                reply = await self.bus.call(Message(destination=PORTAL, path=PORTAL_PATH,
                                                    interface=SHORTCUTS, member=member,
                                                    signature=signature, body=args + [options]))
                if reply.message_type == MessageType.ERROR:
                    if not self.registered:
                        raise AppError("HOTKEY", "Shortcut portal needs the app's desktop entry; "
                                                 "run 'dictation4me init' once, then restart the service")
                    raise AppError("HOTKEY", "Portal refused shortcut registration: "
                                             + str(reply.error_name or "error"))
                handle = reply.body[0]
                async with asyncio.timeout(90):
                    while handle not in responses:
                        signaled.clear()
                        await signaled.wait()
                code, results = responses[handle]
                if code != 0:
                    raise AppError("HOTKEY", "Shortcut permission rejected")
                return results
            finally:
                self.bus.remove_message_handler(receive)
                await self.bus.call(Message(destination="org.freedesktop.DBus",
                                            path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
                                            member="RemoveMatch", signature="s", body=[response_rule]))

        # Host app identity registration. Newer portals require it for
        # GlobalShortcuts and only accept app IDs with an installed .desktop
        # entry. Older portals lack the Registry interface entirely.
        with contextlib.suppress(Exception):
            reply = await asyncio.wait_for(self.bus.call(Message(
                destination=PORTAL, path=PORTAL_PATH, interface="org.freedesktop.host.portal.Registry",
                member="Register", signature="sa{sv}", body=[APP_ID, {}])), timeout=3)
            self.registered = (reply.message_type != MessageType.ERROR or
                               reply.error_name == "org.freedesktop.DBus.Error.UnknownMethod")
        created = await request("CreateSession", "a{sv}", [],
                                {"session_handle_token": Variant("s", "s" + secrets.token_hex(8))})
        self.session = created["session_handle"].value
        mapping = {"ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT", "super": "LOGO", "meta": "LOGO"}
        parts = self.value.lower().split("+")
        trigger = "+".join([mapping[m] for m in parts[:-1]] + [parts[-1].upper()])
        result = await request("BindShortcuts", "oa(sa{sv})sa{sv}", [
            self.session, [["dictate", {"description": Variant("s", "Start / stop / deliver dictation"),
                                       "preferred_trigger": Variant("s", trigger)}]], ""], {})
        shortcuts = result.get("shortcuts", Variant("a(sa{sv})", [])).value
        self.description = next((attrs.get("trigger_description", Variant("s", self.value)).value
                                 for name, attrs in shortcuts if name == "dictate"), self.value)
        self.ready.set()
        await self._stop.wait()
        if self.session:
            with contextlib.suppress(Exception):
                await self.bus.call(Message(destination=PORTAL, path=self.session,
                                            interface="org.freedesktop.portal.Session", member="Close"))
        self.bus.disconnect()

    async def _match(self, rule):
        from dbus_next import Message
        await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                    interface="org.freedesktop.DBus", member="AddMatch",
                                    signature="s", body=[rule]))

    def close(self):
        if self.loop and self._stop:
            self.loop.call_soon_threadsafe(self._stop.set)
        if self.thread:
            self.thread.join(timeout=3)
