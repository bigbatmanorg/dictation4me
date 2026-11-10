"""Serialized session state with cancellable, generation-safe batch work."""
from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
import time
from typing import Callable

from .audio import Recorder
from .config import Config
from .stt import transcribe


class Controller:
    def __init__(self, config: Config, desktop, *, recorder=None, transcriber=transcribe,
                 changed: Callable[[dict], None] | None = None):
        self.config = config
        self.desktop = desktop
        self.recorder = recorder or Recorder()
        self.transcriber = transcriber
        self.changed = changed or (lambda status: None)
        self.state = "idle"
        self.message = "Ready"
        self.last_text = ""
        self.retry_wav: bytes | None = None
        self.work: asyncio.Task | None = None
        self.timer: asyncio.Task | None = None
        self.started = 0.0
        self.lock = asyncio.Lock()
        self._session_config: Config | None = None
        self._retry_timer: asyncio.Task | None = None
        self._last_hotkey = 0.0

    def status(self) -> dict:
        return {"state": self.state, "message": self.message,
                "seconds": int(time.monotonic() - self.started) if self.state == "recording" else 0,
                "level": round(getattr(self.recorder, "level", 0.0), 4),
                "can_retry": self.retry_wav is not None and self.state == "idle",
                "has_last_text": bool(self.last_text)}

    def update(self, state: str, message: str) -> None:
        self.state, self.message = state, message
        self.changed(self.status())

    async def hotkey(self) -> None:
        now = time.monotonic()
        if now - self._last_hotkey < 0.35:
            return
        self._last_hotkey = now
        await self.command("toggle")

    async def command(self, action: str) -> dict:
        async with self.lock:
            if action == "toggle":
                action = "stop-recording" if self.state == "recording" else "start-recording"
            if action == "start-recording":
                if self.state != "idle":
                    return {"ok": False, "error": "Busy; finish or cancel the current operation"}
                self._session_config = deepcopy(self.config)
                self.clear_retry()
                try:
                    await asyncio.to_thread(self.recorder.start, self._session_config.audio)
                    self.started = time.monotonic()
                    self.update("recording", "Recording — press the shortcut again to stop")
                    self.timer = asyncio.create_task(self._watch_recording())
                except Exception as exc:
                    self.update("idle", str(exc))
                    return {"ok": False, "error": str(exc)}
            elif action == "stop-recording":
                if self.state != "recording":
                    return {"ok": False, "error": "Not recording"}
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
                self.update("transcribing", "Preparing audio…")
                try:
                    recording = await asyncio.to_thread(self.recorder.stop)
                except Exception as exc:
                    self.update("idle", str(exc))
                    return {"ok": False, "error": str(exc)}
                self.work = asyncio.create_task(self._finish(recording))
            elif action == "cancel":
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
                if self.work:
                    self.work.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.work
                    self.work = None
                await asyncio.to_thread(self.recorder.abort)
                self.clear_retry()
                self.update("idle", "Cancelled (already-sent keystrokes cannot be recalled)")
            elif action == "retry":
                if self.state != "idle" or self.retry_wav is None:
                    return {"ok": False, "error": "No retained recording to retry"}
                self._session_config = deepcopy(self.config)
                self.update("transcribing", "Retrying transcription…")
                self.work = asyncio.create_task(self._finish(None))
            elif action in ("copy-last", "paste-last"):
                if self.state != "idle" or not self.last_text:
                    return {"ok": False, "error": "No completed transcript, or operation is busy"}
                self.update("inserting", "Copying last transcript…")
                self.work = asyncio.create_task(self._deliver(self.last_text, self.config,
                                                             paste=action == "paste-last"))
            else:
                return {"ok": False, "error": "Unknown action"}
        return {"ok": True, **self.status()}

    async def _watch_recording(self) -> None:
        config = self._session_config or self.config
        try:
            while self.state == "recording":
                await asyncio.sleep(0.2)
                if (getattr(self.recorder, "error", "")
                        or getattr(self.recorder, "limit_reached", False)
                        or time.monotonic() - self.started >= config.audio.max_seconds):
                    # Do not cancel the timer task from inside command().
                    self.timer = None
                    await self.command("stop-recording")
                    return
        except asyncio.CancelledError:
            pass

    def clear_retry(self) -> None:
        self.retry_wav = None
        if self._retry_timer:
            self._retry_timer.cancel()
            self._retry_timer = None

    async def _expire_retry(self, retained: bytes) -> None:
        await asyncio.sleep(300)
        if self.retry_wav is retained:
            self.retry_wav = None
            self.changed(self.status())

    async def _finish(self, recording) -> None:
        cfg = self._session_config or self.config
        try:
            if recording is not None:
                self.retry_wav = await asyncio.to_thread(recording.wav)
            if self.retry_wav is None:
                raise RuntimeError("Recording expired; please record again")
            self.update("transcribing", "Transcribing…")
            text = await self.transcriber(self.retry_wav, cfg.service)
            self.clear_retry()
            self.last_text = text
            self.update("inserting", "Copying transcript…")
            await self._deliver(text, cfg, paste=cfg.output.auto_paste)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.retry_wav is not None:
                if self._retry_timer:
                    self._retry_timer.cancel()
                self._retry_timer = asyncio.create_task(self._expire_retry(self.retry_wav))
            self.update("idle", str(exc))

    async def _deliver(self, text: str, cfg: Config, *, paste: bool) -> None:
        copied = False
        try:
            await self.desktop.copy(text)
            copied = True
            if paste and cfg.output.backend != "clipboard":
                await asyncio.sleep(cfg.output.paste_delay_ms / 1000)
                if await self.desktop.read_clipboard() != text:
                    self.update("idle", "Clipboard changed; paste skipped. Use Copy last to recover the transcript")
                    return
                await self.desktop.paste(cfg.output.paste_shortcut)
                self.update("idle", "Paste shortcut sent; transcript also available in clipboard")
            else:
                self.update("idle", "Transcript copied; paste it manually")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            location = "clipboard" if copied else "app memory (Copy last)"
            self.update("idle", f"Output failed: {exc}. Transcript retained in {location}")

    async def close(self) -> None:
        await self.command("cancel")
        self.last_text = ""
        await self.desktop.close()
