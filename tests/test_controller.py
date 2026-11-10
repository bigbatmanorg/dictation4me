import asyncio

import pytest

from dictation4me.config import Config
from dictation4me.controller import Controller


class FakeRecording:
    def wav(self):
        return b"fake-wav"


class FakeRecorder:
    level = 0.1
    error = ""
    def __init__(self):
        self.active = False
        self.starts = 0
    def start(self, config):
        self.active = True
        self.starts += 1
    def stop(self):
        self.active = False
        return FakeRecording()
    def abort(self):
        self.active = False


class FakeDesktop:
    def __init__(self):
        self.clipboard = ""
        self.pastes = []
        self.fail_copy = False
        self.fail_paste = False
        self.external_clipboard = None
    async def copy(self, text):
        if self.fail_copy:
            raise RuntimeError("Clipboard failed")
        self.clipboard = text
    async def read_clipboard(self):
        return self.external_clipboard if self.external_clipboard is not None else self.clipboard
    async def paste(self, shortcut):
        if self.fail_paste:
            raise RuntimeError("Injection failed")
        self.pastes.append((shortcut, self.clipboard))
    async def prepare(self):
        pass
    async def close(self):
        pass


async def good_transcribe(wav, config):
    return "Unicode text: ěščřž — 😀"


def make(transcriber=good_transcribe):
    cfg = Config()
    cfg.output.paste_delay_ms = 0
    desktop = FakeDesktop()
    controller = Controller(cfg, desktop, recorder=FakeRecorder(), transcriber=transcriber)
    return controller, desktop


async def record(controller):
    assert (await controller.command("start-recording"))["ok"]
    assert (await controller.command("stop-recording"))["ok"]
    await controller.work


async def test_complete_cycle_and_last_copy():
    c, d = make()
    await record(c)
    assert c.state == "idle"
    assert len(d.pastes) == 1
    assert c.last_text == d.clipboard
    assert c.retry_wav is None
    assert "Unicode" not in str(c.status())
    d.clipboard = "other text"
    await c.command("copy-last")
    await c.work
    assert d.clipboard == c.last_text
    assert len(d.pastes) == 1
    await c.close()


async def test_clipboard_only():
    c, d = make()
    c.config.output.auto_paste = False
    await record(c)
    assert d.clipboard and not d.pastes
    await c.close()


async def test_no_duplicate_start_or_stop():
    c, d = make()
    assert (await c.command("start-recording"))["ok"]
    assert not (await c.command("start-recording"))["ok"]
    assert c.recorder.starts == 1
    assert (await c.command("stop-recording"))["ok"]
    assert not (await c.command("stop-recording"))["ok"]
    await c.work
    assert len(d.pastes) == 1
    await c.close()


async def test_cancel_network_and_no_late_paste():
    gate = asyncio.Event()
    async def slow(wav, config):
        await gate.wait()
        return "must not paste"
    c, d = make(slow)
    await c.command("start-recording")
    await c.command("stop-recording")
    await asyncio.sleep(0.02)
    await c.command("cancel")
    gate.set()
    await asyncio.sleep(0.02)
    assert c.state == "idle" and not d.pastes and not d.clipboard
    assert c.retry_wav is None
    await c.close()


async def test_cancel_insertion_delay():
    c, d = make()
    c.config.output.paste_delay_ms = 1000
    await c.command("start-recording")
    await c.command("stop-recording")
    for _ in range(50):
        if d.clipboard:
            break
        await asyncio.sleep(0.01)
    assert d.clipboard
    await c.command("cancel")
    assert not d.pastes
    await c.close()


async def test_retry_after_failure_exactly_one_paste():
    calls = 0
    async def transcriber(wav, config):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Network unavailable")
        return "retried"
    c, d = make(transcriber)
    await record(c)
    assert c.status()["can_retry"] and not d.pastes
    await c.command("retry")
    await c.work
    assert calls == 2 and len(d.pastes) == 1 and c.retry_wav is None
    await c.close()


@pytest.mark.parametrize("what", ["copy", "paste"])
async def test_output_failure_keeps_text(what):
    c, d = make()
    setattr(d, f"fail_{what}", True)
    await record(c)
    assert c.last_text and c.state == "idle" and "Output failed" in c.message
    await c.close()


async def test_external_clipboard_change_skips_paste():
    c, d = make()
    d.external_clipboard = "password copied from elsewhere"
    await record(c)
    assert not d.pastes and c.last_text
    assert "Clipboard changed" in c.message
    await c.close()


async def test_session_snapshot_does_not_drift():
    cfg_seen = []
    async def transcriber(wav, cfg):
        cfg_seen.append(cfg.model)
        return "text"
    c, d = make(transcriber)
    await c.command("start-recording")
    c.config.service.model = "new-model"
    await c.command("stop-recording")
    await c.work
    assert cfg_seen == ["whisper-1"]
    await c.close()


async def test_max_duration_auto_stops():
    c, d = make()
    c.config.audio.max_seconds = 1
    await c.command("start-recording")
    c.started -= 2
    await asyncio.sleep(0.25)
    await c.work
    assert c.state == "idle" and len(d.pastes) == 1
    await c.close()


async def test_hotkey_debounce():
    c, d = make()
    await c.hotkey()
    await c.hotkey()
    assert c.state == "recording" and c.recorder.starts == 1
    await c.close()
