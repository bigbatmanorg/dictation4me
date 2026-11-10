import asyncio
import json

from dictation4me.cli import main
from dictation4me.config import Config, save
from dictation4me.ipc import request
from dictation4me.app import Service
from test_controller import FakeDesktop, FakeRecorder, good_transcribe


def test_cli_headless_help_and_init(capsys):
    assert main(["init", "--no-gui", "--no-autostart"]) == 0
    assert "Installed" in capsys.readouterr().out
    assert main(["paths"]) == 0
    assert "config.toml" in capsys.readouterr().out


def test_cli_no_service(capsys):
    assert main(["status"]) == 2
    assert "not running" in capsys.readouterr().err


async def test_resident_service_control_end_to_end():
    cfg = Config()
    cfg.output.paste_delay_ms = 0
    save(cfg)
    desktop = FakeDesktop()
    service = Service(no_tray=True, no_hotkey=True, desktop=desktop,
                      recorder=FakeRecorder(), transcriber=good_transcribe)
    task = asyncio.create_task(service.run())
    try:
        for _ in range(50):
            try:
                status = await asyncio.to_thread(request, "status", timeout=0.2)
                break
            except ConnectionError:
                await asyncio.sleep(0.02)
        assert status["state"] == "idle"
        assert (await asyncio.to_thread(request, "toggle"))["state"] == "recording"
        assert (await asyncio.to_thread(request, "toggle"))["ok"]
        for _ in range(100):
            if desktop.pastes:
                break
            await asyncio.sleep(0.01)
        assert len(desktop.pastes) == 1
        status = await asyncio.to_thread(request, "status")
        assert "Unicode text" not in json.dumps(status)
        assert status["has_last_text"]
        assert (await asyncio.to_thread(request, "reload"))["ok"]
        assert not (await asyncio.to_thread(request, "not-a-command"))["ok"]
        await asyncio.to_thread(request, "shutdown")
        await asyncio.wait_for(task, 3)
    finally:
        if not task.done():
            service.shutdown.set()
            await task
