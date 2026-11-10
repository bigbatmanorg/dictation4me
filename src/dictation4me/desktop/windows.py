"""Win32 clipboard, RegisterHotKey and SendInput; imported on Windows only at runtime."""
from __future__ import annotations

import asyncio
import ctypes as C
from ctypes import wintypes as W
import threading

from ..config import OutputConfig, parse_hotkey


class DesktopError(RuntimeError):
    pass


# Explicit Win32 widths also make ABI layout tests meaningful on non-Windows hosts.
class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", C.c_uint16), ("wScan", C.c_uint16), ("dwFlags", C.c_uint32),
                ("time", C.c_uint32), ("dwExtraInfo", C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", C.c_int32), ("dy", C.c_int32), ("mouseData", C.c_uint32),
                ("dwFlags", C.c_uint32), ("time", C.c_uint32), ("dwExtraInfo", C.c_size_t)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", C.c_uint32), ("wParamL", C.c_uint16), ("wParamH", C.c_uint16)]


class InputUnion(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", C.c_uint32), ("value", InputUnion)]


def keyboard_events(shortcut: str):
    keys = {"ctrl+v": [0x11, 0x56], "ctrl+shift+v": [0x11, 0x10, 0x56],
            "shift+insert": [0x10, 0x2D]}[shortcut]
    events = []
    for key in keys:
        events.append(INPUT(type=1, ki=KEYBDINPUT(wVk=key)))
    for key in reversed(keys):
        events.append(INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=2)))
    return (INPUT * len(events))(*events)


def hotkey_codes(value: str) -> tuple[int, int]:
    mods, key = parse_hotkey(value)
    flags = sum({"alt": 1, "ctrl": 2, "shift": 4, "super": 8}[m] for m in mods)
    code = 0x6F + int(key[1:]) if key.startswith("f") and len(key) > 1 else ord(key.upper())
    return flags | 0x4000, code  # MOD_NOREPEAT


class WinAPI:
    def __init__(self):
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        # Pointer-sized handles must never use ctypes' default c_int restype.
        prototypes = {
            "OpenClipboard": ([W.HWND], W.BOOL), "CloseClipboard": ([], W.BOOL),
            "EmptyClipboard": ([], W.BOOL), "SetClipboardData": ([W.UINT, W.HANDLE], W.HANDLE),
            "GetClipboardData": ([W.UINT], W.HANDLE),
            "IsClipboardFormatAvailable": ([W.UINT], W.BOOL),
            "SendInput": ([W.UINT, C.POINTER(INPUT), C.c_int], W.UINT),
            "GetAsyncKeyState": ([C.c_int], C.c_short),
            "RegisterHotKey": ([W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL),
            "UnregisterHotKey": ([W.HWND, C.c_int], W.BOOL),
            "GetMessageW": ([C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL),
            "PeekMessageW": ([C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT], W.BOOL),
            "PostThreadMessageW": ([W.DWORD, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            "CreateWindowExW": ([W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                  C.c_int, C.c_int, C.c_int, C.c_int,
                                  W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND),
            "DestroyWindow": ([W.HWND], W.BOOL),
            "TranslateMessage": ([C.POINTER(W.MSG)], W.BOOL),
            "DispatchMessageW": ([C.POINTER(W.MSG)], W.LPARAM),
        }
        for name, (args, result) in prototypes.items():
            fun = getattr(self.user, name)
            fun.argtypes, fun.restype = args, result
        for name, args, result in [
            ("GlobalAlloc", [W.UINT, C.c_size_t], W.HGLOBAL),
            ("GlobalLock", [W.HGLOBAL], W.LPVOID),
            ("GlobalUnlock", [W.HGLOBAL], W.BOOL),
            ("GlobalFree", [W.HGLOBAL], W.HGLOBAL),
            ("GlobalSize", [W.HGLOBAL], C.c_size_t),
            ("GetCurrentThreadId", [], W.DWORD),
        ]:
            fun = getattr(self.kernel, name)
            fun.argtypes, fun.restype = args, result


class WindowsDesktop:
    def __init__(self, config: OutputConfig):
        self.config = config
        self.api = WinAPI()
        # A real hidden message-only window owns clipboard allocations. Opening
        # with NULL and then EmptyClipboard leaves no owner for SetClipboardData.
        self.hwnd = self.api.user.CreateWindowExW(0, "STATIC", "Dictation4me", 0,
                                                  0, 0, 0, 0, W.HWND(-3), None, None, None)
        if not self.hwnd:
            raise DesktopError("Cannot create the Win32 clipboard owner window")
        self.message_task = None

    async def _pump_messages(self) -> None:
        msg = W.MSG()
        while self.hwnd:
            while self.api.user.PeekMessageW(C.byref(msg), self.hwnd, 0, 0, 1):
                self.api.user.TranslateMessage(C.byref(msg))
                self.api.user.DispatchMessageW(C.byref(msg))
            await asyncio.sleep(0.02)

    async def _open(self) -> None:
        if self.message_task is None:
            self.message_task = asyncio.create_task(self._pump_messages())
        for _ in range(30):
            if self.api.user.OpenClipboard(self.hwnd):
                return
            await asyncio.sleep(0.02)
        raise DesktopError("Clipboard is busy; try Copy last again")

    async def copy(self, text: str) -> None:
        raw = (text + "\0").encode("utf-16-le")
        api = self.api
        handle = api.kernel.GlobalAlloc(0x0002, len(raw))
        if not handle:
            raise DesktopError("Cannot allocate clipboard memory")
        transferred = False
        try:
            pointer = api.kernel.GlobalLock(handle)
            if not pointer:
                raise DesktopError("Cannot lock clipboard memory")
            try:
                C.memmove(pointer, raw, len(raw))
            finally:
                api.kernel.GlobalUnlock(handle)
            await self._open()
            try:
                if not api.user.EmptyClipboard() or not api.user.SetClipboardData(13, handle):
                    raise DesktopError("Cannot set the Windows Unicode clipboard")
                transferred = True
            finally:
                api.user.CloseClipboard()
        finally:
            if not transferred:
                api.kernel.GlobalFree(handle)

    async def read_clipboard(self) -> str:
        api = self.api
        await self._open()
        try:
            if not api.user.IsClipboardFormatAvailable(13):
                return ""
            handle = api.user.GetClipboardData(13)
            pointer = api.kernel.GlobalLock(handle) if handle else None
            if not pointer:
                raise DesktopError("Cannot read clipboard")
            try:
                size = api.kernel.GlobalSize(handle)
                if size > 2 * 1024 * 1024:
                    return ""  # unrelated huge clipboard, never paste it as our transcript
                raw = C.string_at(pointer, size)
                return raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
            finally:
                api.kernel.GlobalUnlock(handle)
        finally:
            api.user.CloseClipboard()

    async def prepare(self) -> None:
        if self.config.backend in ("xdotool", "ydotool"):
            raise DesktopError("Linux paste backend selected on Windows; choose auto")

    async def paste(self, shortcut: str) -> None:
        await self.prepare()
        # Do not release physical keys the user is still holding.
        for _ in range(100):
            if not any(self.api.user.GetAsyncKeyState(k) & 0x8000
                       for k in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
                break
            await asyncio.sleep(0.02)
        else:
            raise DesktopError("Modifier keys are still held; paste skipped")
        events = keyboard_events(shortcut)
        sent = self.api.user.SendInput(len(events), events, C.sizeof(INPUT))
        if sent != len(events):
            # Best effort key-up for modifiers injected before a partial failure.
            release = [event for event in events if event.ki.dwFlags & 2]
            self.api.user.SendInput(len(release), (INPUT * len(release))(*release), C.sizeof(INPUT))
            raise DesktopError("Windows rejected paste input (an elevated or secure target may block it)")

    async def close(self) -> None:
        if self.message_task:
            self.message_task.cancel()
            try:
                await self.message_task
            except asyncio.CancelledError:
                pass
        if self.hwnd:
            self.api.user.DestroyWindow(self.hwnd)
            self.hwnd = None


class WindowsHotkey:
    def __init__(self, value: str, callback):
        self.value, self.callback = value, callback
        self.thread = None
        self.thread_id = None
        self.ready = threading.Event()
        self.error = ""
        self.api = WinAPI()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="dictation-hotkey")
        self.thread.start()
        if not self.ready.wait(3) or self.error:
            raise DesktopError(self.error or "Windows hotkey registration timed out")

    def _run(self) -> None:
        self.thread_id = self.api.kernel.GetCurrentThreadId()
        msg = W.MSG()
        self.api.user.PeekMessageW(C.byref(msg), None, 0, 0, 0)
        mods, code = hotkey_codes(self.value)
        if not self.api.user.RegisterHotKey(None, 1, mods, code):
            self.error = "Global shortcut is already taken; choose another in Settings"
            self.ready.set()
            return
        self.ready.set()
        try:
            while self.api.user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    self.callback()
        finally:
            self.api.user.UnregisterHotKey(None, 1)

    def close(self) -> None:
        if self.thread_id:
            self.api.user.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread:
            self.thread.join(2)
