"""Win32 Unicode clipboard, best-effort SendInput and RegisterHotKey."""
from __future__ import annotations

import ctypes as C
import threading
import time
from ctypes import wintypes as W

from ..errors import AppError

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", C.c_ushort), ("wScan", C.c_ushort), ("dwFlags", C.c_ulong),
                ("time", C.c_ulong), ("dwExtraInfo", C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", C.c_long), ("dy", C.c_long), ("mouseData", C.c_ulong),
                ("dwFlags", C.c_ulong), ("time", C.c_ulong), ("dwExtraInfo", C.c_size_t)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", C.c_ulong), ("wParamL", C.c_ushort), ("wParamH", C.c_ushort)]


class _INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", C.c_ulong), ("value", _INPUTUNION)]


def hotkey_codes(value: str) -> tuple[int, int]:
    parts = value.lower().replace("control", "ctrl").split("+")
    key = parts[-1]
    mods = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "super": 0x8, "meta": 0x8}
    flags = 0x4000  # MOD_NOREPEAT
    for modifier in parts[:-1]:
        if modifier not in mods:
            raise AppError("HOTKEY", "Unsupported hotkey modifier")
        flags |= mods[modifier]
    if key.startswith("f") and key[1:].isdigit():
        code = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key.upper())
    else:
        raise AppError("HOTKEY", "Unsupported hotkey key")
    return flags, code


class WinAPI:
    def __init__(self):
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        proto = {
            "OpenClipboard": ([W.HWND], W.BOOL), "CloseClipboard": ([], W.BOOL),
            "EmptyClipboard": ([], W.BOOL), "SetClipboardData": ([W.UINT, W.HANDLE], W.HANDLE),
            "GetClipboardData": ([W.UINT], W.HANDLE),
            "IsClipboardFormatAvailable": ([W.UINT], W.BOOL),
            "SendInput": ([W.UINT, C.POINTER(INPUT), C.c_int], W.UINT),
            "GetAsyncKeyState": ([C.c_int], C.c_short),
            "RegisterHotKey": ([W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL),
            "UnregisterHotKey": ([W.HWND, C.c_int], W.BOOL),
            "GetMessageW": ([C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], C.c_int),
            "PeekMessageW": ([C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT], W.BOOL),
            "PostThreadMessageW": ([W.DWORD, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            "GetForegroundWindow": ([], W.HWND),
            "CreateWindowExW": ([W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                  C.c_int, C.c_int, C.c_int, C.c_int,
                                  W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND),
            "DestroyWindow": ([W.HWND], W.BOOL),
        }
        for name, (args, ret) in proto.items():
            fn = getattr(self.user, name)
            fn.argtypes, fn.restype = args, ret
        for name, args, ret in [
            ("GlobalAlloc", [W.UINT, C.c_size_t], W.HGLOBAL),
            ("GlobalLock", [W.HGLOBAL], W.LPVOID),
            ("GlobalUnlock", [W.HGLOBAL], W.BOOL),
            ("GlobalFree", [W.HGLOBAL], W.HGLOBAL),
            ("GlobalSize", [W.HGLOBAL], C.c_size_t),
            ("GetCurrentThreadId", [], W.DWORD),
        ]:
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = args, ret


def _keyboard_events(shortcut: str):
    keys = {"ctrl+v": [0x11, 0x56], "ctrl+shift+v": [0x11, 0x10, 0x56],
            "shift+insert": [0x10, 0x2D]}.get(shortcut.lower())
    if not keys:
        raise AppError("INVALID", "Unsupported paste shortcut")
    items = [INPUT(type=1, ki=KEYBDINPUT(wVk=k)) for k in keys]
    items += [INPUT(type=1, ki=KEYBDINPUT(wVk=k, dwFlags=KEYEVENTF_KEYUP)) for k in reversed(keys)]
    return (INPUT * len(items))(*items)


class WindowsDesktop:
    def __init__(self):
        self.api = WinAPI()
        self.clipboard_lock = threading.RLock()
        # Clipboard ownership is explicit; null owner plus EmptyClipboard can
        # make SetClipboardData fail on Windows.
        self.owner = self.api.user.CreateWindowExW(0, "STATIC", "Dictation4me", 0, 0, 0, 0, 0,
                                                   W.HWND(-3), None, None, None)
        if not self.owner:
            raise AppError("DESKTOP", "Unable to create Windows clipboard owner")

    def _open(self) -> None:
        for _ in range(30):
            if self.api.user.OpenClipboard(self.owner):
                return
            time.sleep(.025)
        raise AppError("CLIPBOARD", "Clipboard is busy")

    def copy(self, text: str) -> None:
        raw = (text + "\0").encode("utf-16-le")
        with self.clipboard_lock:
            handle = self.api.kernel.GlobalAlloc(GMEM_MOVEABLE, len(raw))
            if not handle:
                raise AppError("CLIPBOARD", "Unable to allocate clipboard memory")
            transferred = False
            try:
                ptr = self.api.kernel.GlobalLock(handle)
                if not ptr:
                    raise AppError("CLIPBOARD", "Unable to lock clipboard memory")
                try:
                    C.memmove(ptr, raw, len(raw))
                finally:
                    self.api.kernel.GlobalUnlock(handle)
                self._open()
                try:
                    if not self.api.user.EmptyClipboard() or not self.api.user.SetClipboardData(CF_UNICODETEXT, handle):
                        raise AppError("CLIPBOARD", "Unable to set Unicode clipboard")
                    transferred = True
                finally:
                    self.api.user.CloseClipboard()
            finally:
                if not transferred:
                    self.api.kernel.GlobalFree(handle)

    def read(self) -> str:
        with self.clipboard_lock:
            self._open()
            try:
                if not self.api.user.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return ""
                handle = self.api.user.GetClipboardData(CF_UNICODETEXT)
                ptr = self.api.kernel.GlobalLock(handle) if handle else None
                if not ptr:
                    raise AppError("CLIPBOARD", "Unable to read Unicode clipboard")
                try:
                    size = self.api.kernel.GlobalSize(handle)
                    if size > 2 * 1024 * 1024:
                        raise AppError("CLIPBOARD", "Clipboard content is too large to verify")
                    return C.string_at(ptr, size).decode("utf-16-le", errors="replace").split("\0", 1)[0]
                finally:
                    self.api.kernel.GlobalUnlock(handle)
            finally:
                self.api.user.CloseClipboard()

    def foreground_token(self) -> str | None:
        value = self.api.user.GetForegroundWindow()
        return str(value) if value else None

    def paste(self, shortcut: str, *, expected_target: str | None = None) -> str:
        if not expected_target or self.foreground_token() != expected_target:
            return "blocked"
        # Do not inject while a shortcut's modifier is still physically pressed.
        for _ in range(80):
            if not any(self.api.user.GetAsyncKeyState(k) & 0x8000
                       for k in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
                break
            time.sleep(.025)
        else:
            return "blocked"
        events = _keyboard_events(shortcut)
        sent = self.api.user.SendInput(len(events), events, C.sizeof(INPUT))
        if sent != len(events):
            release = [event for event in events if event.ki.dwFlags & KEYEVENTF_KEYUP]
            if release:
                self.api.user.SendInput(len(release), (INPUT * len(release))(*release), C.sizeof(INPUT))
            return "blocked"
        return "sent"  # accepted text remains unobservable

    def close(self) -> None:
        if self.owner:
            self.api.user.DestroyWindow(self.owner)
            self.owner = None


class WindowsHotkey:
    def __init__(self, value: str, callback):
        self.value, self.callback = value, callback
        self.api = WinAPI()
        self.ready = threading.Event()
        self.thread = None
        self.thread_id = 0
        self.error = ""

    def start(self):
        self.thread = threading.Thread(target=self._run, name="dictation-global-hotkey", daemon=True)
        self.thread.start()
        if not self.ready.wait(4) or self.error:
            raise AppError("HOTKEY", self.error or "Windows shortcut registration timed out")

    def _run(self):
        self.thread_id = self.api.kernel.GetCurrentThreadId()
        msg = W.MSG()
        self.api.user.PeekMessageW(C.byref(msg), None, 0, 0, 0)
        mods, code = hotkey_codes(self.value)
        if not self.api.user.RegisterHotKey(None, 1, mods, code):
            self.error = "Shortcut already assigned elsewhere; change it in Settings"
            self.ready.set()
            return
        self.ready.set()
        try:
            while self.api.user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.callback()
        finally:
            self.api.user.UnregisterHotKey(None, 1)

    def close(self):
        if self.thread_id:
            self.api.user.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        if self.thread:
            self.thread.join(timeout=3)
