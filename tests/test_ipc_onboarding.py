import asyncio
import ctypes
import json
import sys

import pytest
from dictation4me import paths
from dictation4me.config import save
from dictation4me.ipc import AlreadyRunning, Server, request
from dictation4me.onboarding import desktop_quote, initialize, install_integration, remove_integration, interpreter
from dictation4me.desktop.windows import INPUT, keyboard_events, hotkey_codes


async def test_ipc_auth_status_and_single_instance(tmp_path):
    async def handler(action):
        return {"ok": True, "action": action}
    server = Server(handler, tmp_path / "runtime")
    await server.start()
    try:
        response = await asyncio.to_thread(request, "status", directory=server.directory)
        assert response == {"ok": True, "action": "status"}
        another = Server(handler, server.directory)
        with pytest.raises(AlreadyRunning):
            await another.start()
        endpoint = json.loads(server.path.read_text())
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint["port"])
        writer.write(b'{"action":"shutdown","token":"wrong"}\n')
        await writer.drain()
        response = json.loads(await reader.readline())
        assert not response["ok"]
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
    assert not server.path.exists()
    fresh = Server(handler, server.directory)
    await fresh.start()
    await fresh.close()


async def test_invalid_json_does_not_crash_server(tmp_path):
    async def handler(action):
        return {"ok": True}
    server = Server(handler, tmp_path)
    await server.start()
    port = json.loads(server.path.read_text())["port"]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"not json\n")
    await writer.drain()
    assert await reader.read() == b""
    writer.close()
    await writer.wait_closed()
    assert (await asyncio.to_thread(request, "status", directory=tmp_path))["ok"]
    await server.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux desktop files")
def test_onboarding_idempotent_preserves_settings():
    cfg, entries = initialize()
    assert len(entries) == 2
    cfg.service.api_key = "keep-this-secret"
    save(cfg)
    cfg2, entries2 = initialize()
    assert cfg2.service.api_key == "keep-this-secret"
    assert entries == entries2
    assert "keep-this-secret" not in entries[0].read_text()
    assert "-m" in entries[0].read_text()
    cfg2.desktop.autostart = False
    install_integration(cfg2)
    assert entries[0].exists() and not entries[1].exists()
    remove_integration()
    assert paths.config_file().exists() and not entries[0].exists()


def test_interpreter_preserves_virtual_environment_symlink(tmp_path, monkeypatch):
    target = tmp_path / "system-python"
    target.touch()
    venv = tmp_path / "venv" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    try:
        venv.symlink_to(target)
    except OSError:
        pytest.skip("Symlink permission unavailable")
    monkeypatch.setattr(sys, "executable", str(venv))
    assert interpreter() == str(venv)


def test_desktop_quoting():
    assert desktop_quote("/tmp/my app/python") == '"/tmp/my app/python"'
    assert "%%" in desktop_quote("x%y")
    with pytest.raises(ValueError):
        desktop_quote("line\nbreak")


def test_windows_input_abi_and_release_order():
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    events = keyboard_events("ctrl+shift+v")
    assert [e.ki.wVk for e in events] == [0x11, 0x10, 0x56, 0x56, 0x10, 0x11]
    assert [e.ki.dwFlags for e in events] == [0, 0, 0, 2, 2, 2]
    assert hotkey_codes("ctrl+alt+d") == (0x4003, ord("D"))
    assert hotkey_codes("ctrl+f11") == (0x4002, 0x7A)
