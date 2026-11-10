import socket
import sys

import pytest

from dictation4me.desktop.linux import paste_args, session_type, socket_usable


@pytest.mark.parametrize("shortcut,codes", [("ctrl+v", [29,47]), ("ctrl+shift+v", [29,42,47]), ("shift+insert", [42,110])])
def test_balanced_paste_keys(shortcut, codes):
    args = paste_args("ydotool", shortcut)
    assert args == ["key"] + [f"{k}:1" for k in codes] + [f"{k}:0" for k in reversed(codes)]
    assert "--clearmodifiers" in paste_args("xdotool", shortcut)


def test_wayland_wins_over_xwayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("DISPLAY", ":0")
    assert session_type() == "wayland"
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert session_type() == "x11"
    monkeypatch.delenv("DISPLAY")
    assert session_type() == "headless"


@pytest.mark.skipif(sys.platform == "win32", reason="Unix socket probing")
@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_socket_probe_stale_and_live(tmp_path, kind):
    path = str(tmp_path / "daemon.sock")
    with socket.socket(socket.AF_UNIX, kind) as server:
        server.bind(path)
        if kind == socket.SOCK_STREAM:
            server.listen(1)
        assert socket_usable(path)
    assert not socket_usable(path)


@pytest.mark.dbus
def test_dbus_interfaces_import_and_introspect():
    pytest.importorskip("dbus_next")
    from dictation4me.desktop.linux_bus import Menu, Tray
    assert Menu(lambda action: None).introspect().name == "com.canonical.dbusmenu"
    assert Tray(lambda action: None).introspect().name == "org.kde.StatusNotifierItem"


@pytest.mark.desktop
def test_real_x11_hotkey_on_dedicated_test_display():
    """Opt-in only: never synthesize keys on a developer's normal desktop."""
    import os
    import threading
    import time
    if sys.platform != "linux" or os.environ.get("DICTATION4ME_TEST_X11") != "1":
        pytest.skip("Use DICTATION4ME_TEST_X11=1 with a dedicated Xvfb display")
    pytest.importorskip("Xlib")
    from Xlib import X, XK, display
    from Xlib.ext import xtest
    from dictation4me.desktop.x11_hotkey import X11Hotkey
    activated = threading.Event()
    hotkey = X11Hotkey("ctrl+alt+d", activated.set)
    connection = display.Display()
    keys = [connection.keysym_to_keycode(XK.string_to_keysym(name))
            for name in ("Control_L", "Alt_L", "d")]
    try:
        hotkey.start()
        for key in keys:
            xtest.fake_input(connection, X.KeyPress, key)
        connection.sync()
        assert activated.wait(2), "Passive X11 grab did not receive the shortcut"
        for key in reversed(keys):
            xtest.fake_input(connection, X.KeyRelease, key)
        connection.sync()
        time.sleep(0.05)
    finally:
        for key in reversed(keys):
            xtest.fake_input(connection, X.KeyRelease, key)
        connection.sync()
        hotkey.close()
        connection.close()
