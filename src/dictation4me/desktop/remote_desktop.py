"""Keyboard input through the freedesktop RemoteDesktop portal (KDE Plasma Wayland).

This is the unprivileged way to send the paste shortcut on Wayland: the desktop
asks the user once, and a restore token lets later sessions start without a
dialog. No uinput access, root helper or daemon is involved. Each paste uses
a short-lived session that is closed immediately afterwards.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import threading
from pathlib import Path

from .. import APP_ID
from ..errors import AppError

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
KEYBOARD = 1
PERSIST_UNTIL_REVOKED = 2
CONSENT_TIMEOUT = 120  # the first request waits for the user to answer the dialog


class RemoteDesktopKeyboard:
    def __init__(self, token_path: Path):
        self.token_path = token_path
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._bus = None
        self._session: str | None = None
        self._guard = threading.Lock()

    # ── token storage (private file in the state directory) ──
    def _token(self) -> str:
        try:
            return self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _save_token(self, token: str) -> None:
        if not token:
            return
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(token)

    def forget(self) -> None:
        self.token_path.unlink(missing_ok=True)

    @property
    def authorized(self) -> bool:
        return bool(self._token())

    # ── public API (thread-safe, blocking) ──
    def authorize(self) -> dict:
        """Ask for (or restore) permission without sending any key."""
        return self._run(self._ensure_session(), CONSENT_TIMEOUT + 10) or {"authorized": True}

    def send_keys(self, keycodes: list[int]) -> dict:
        """Press keycodes in order, release in reverse (evdev codes, e.g. Ctrl=29, V=47)."""
        try:
            return self._run(self._send(keycodes), CONSENT_TIMEOUT + 10)
        except AppError as exc:
            if exc.code != "PASTE_SESSION_LOST":
                raise
            return self._run(self._send(keycodes), CONSENT_TIMEOUT + 10)  # one fresh session

    def close(self) -> None:
        loop = self._loop
        if loop:
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(self._close_session(), loop).result(3)
            loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=3)
        self._loop = self._thread = None

    # ── event loop thread ──
    def _run(self, coroutine, timeout: float):
        with self._guard:
            if self._loop is None:
                ready = threading.Event()

                def runner():
                    self._loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self._loop)
                    ready.set()
                    self._loop.run_forever()

                self._thread = threading.Thread(target=runner, name="dictation-remote-desktop", daemon=True)
                self._thread.start()
                ready.wait(3)
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout)
        except TimeoutError as exc:
            future.cancel()
            raise AppError("PASTE_TIMEOUT", "No answer to the keyboard access request") from exc

    async def _send(self, keycodes: list[int]) -> dict:
        from dbus_next import Message, MessageType
        await self._ensure_session()
        for code, state in [(k, 1) for k in keycodes] + [(k, 0) for k in reversed(keycodes)]:
            reply = await self._bus.call(Message(destination=PORTAL, path=PORTAL_PATH, interface=REMOTE,
                                                 member="NotifyKeyboardKeycode", signature="oa{sv}iu",
                                                 body=[self._session, {}, code, state]))
            if reply.message_type == MessageType.ERROR:
                self._session = None  # closed by the desktop (e.g. revoked); retry once
                raise AppError("PASTE_SESSION_LOST", "Keyboard access session ended")
            await asyncio.sleep(0.012)
        return {"sent": bool(keycodes), "authorized": True}

    async def _close_session(self):
        from dbus_next import Message
        if self._bus and self._session:
            with contextlib.suppress(Exception):
                await self._bus.call(Message(destination=PORTAL, path=self._session,
                                             interface="org.freedesktop.portal.Session", member="Close"))
        self._session = None
        if self._bus:
            self._bus.disconnect()
            self._bus = None

    async def _ensure_session(self):
        if self._session and self._bus and self._bus.connected:
            return None
        try:
            from dbus_next import Message, MessageType, Variant
            from dbus_next.aio import MessageBus
        except ImportError as exc:
            raise AppError("PASTE_UNAVAILABLE", "dbus-next is not installed (reinstall dictation4me)") from exc
        if not (self._bus and self._bus.connected):
            self._bus = await MessageBus().connect()
            bus = self._bus

            def closed(msg):
                if (msg.message_type == MessageType.SIGNAL and msg.member == "Closed"
                        and msg.interface == "org.freedesktop.portal.Session" and msg.path == self._session):
                    self._session = None
            bus.add_message_handler(closed)
            await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                   interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                                   body=["type='signal',interface='org.freedesktop.portal.Session'"]))
            # Host apps must register their ID (needs the installed .desktop entry).
            with contextlib.suppress(Exception):
                await asyncio.wait_for(bus.call(Message(
                    destination=PORTAL, path=PORTAL_PATH,
                    interface="org.freedesktop.host.portal.Registry", member="Register",
                    signature="sa{sv}", body=[APP_ID, {}])), timeout=3)
        bus = self._bus

        async def request(member, signature, args, options, timeout=15):
            token = "r" + secrets.token_hex(8)
            options = dict(options, handle_token=Variant("s", token))
            done, answer = asyncio.Event(), {}

            def receive(msg):
                if (msg.message_type == MessageType.SIGNAL and msg.member == "Response"
                        and msg.interface == "org.freedesktop.portal.Request"):
                    answer[msg.path] = msg.body
                    done.set()

            bus.add_message_handler(receive)
            rule = "type='signal',interface='org.freedesktop.portal.Request',member='Response'"
            await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                   interface="org.freedesktop.DBus", member="AddMatch",
                                   signature="s", body=[rule]))
            try:
                reply = await bus.call(Message(destination=PORTAL, path=PORTAL_PATH, interface=REMOTE,
                                               member=member, signature=signature, body=args + [options]))
                if reply.message_type == MessageType.ERROR:
                    raise AppError("PASTE_UNAVAILABLE",
                                   "The desktop refused keyboard access (" + str(reply.error_name) + ")")
                handle = reply.body[0]
                try:
                    async with asyncio.timeout(timeout):
                        while handle not in answer:
                            done.clear()
                            await done.wait()
                except TimeoutError as exc:
                    raise AppError("PASTE_TIMEOUT", "No answer to the keyboard access request") from exc
            finally:
                bus.remove_message_handler(receive)
            code, results = answer[handle]
            if code == 1:
                raise AppError("PASTE_DENIED", "Keyboard access was declined")
            if code != 0:
                raise AppError("PASTE_UNAVAILABLE", "The desktop could not start keyboard access")
            return results

        created = await request("CreateSession", "a{sv}", [],
                                {"session_handle_token": Variant("s", "s" + secrets.token_hex(8))})
        session = created["session_handle"].value
        select = {"types": Variant("u", KEYBOARD), "persist_mode": Variant("u", PERSIST_UNTIL_REVOKED)}
        if self._token():
            select["restore_token"] = Variant("s", self._token())
        await request("SelectDevices", "oa{sv}", [session], select)
        started = await request("Start", "osa{sv}", [session, ""], {}, timeout=CONSENT_TIMEOUT)
        token = started.get("restore_token")
        if token is not None:
            self._save_token(token.value)
        devices = started.get("devices")
        if devices is not None and not devices.value & KEYBOARD:
            raise AppError("PASTE_DENIED", "Keyboard access was not granted")
        self._session = session
        return None
