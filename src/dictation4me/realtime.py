"""Streaming transcription over an OpenAI-style realtime websocket (vLLM / LiteLLM).

Protocol (vLLM realtime ASR, proxied by LiteLLM at /v1/realtime?model=...):
  <- session.created
  -> session.update {model};  -> input_audio_buffer.commit   (start)
  -> input_audio_buffer.append {audio: base64 PCM16 16 kHz mono}  (repeated)
  -> input_audio_buffer.commit {final: true}
  <- transcription.delta {delta} ... <- transcription.done {text}

The WAV recording is still written to disk as usual. This stream only provides
live text and a faster final result; if it fails, the caller transcribes the
saved WAV with the batch model instead, so no dictation is lost.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
from urllib.parse import urlsplit, urlunsplit

from .credentials import resolve
from .errors import AppError

RATE = 16000
MAX_QUEUE = 600  # ~60 s of 100 ms blocks; older audio is never dropped silently


def realtime_url(cfg: dict) -> str:
    parts = urlsplit(cfg["base_url"].rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = parts.path.rstrip("/") + "/" + cfg.get("realtime_path", "/realtime").lstrip("/")
    return urlunsplit((scheme, parts.netloc, path, "model=" + cfg["realtime_model"], ""))


def to_pcm16_mono_16k(block: bytes, rate: int, channels: int) -> bytes:
    import numpy as np
    samples = np.frombuffer(block, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(samples):
        count = max(1, int(round(len(samples) * RATE / rate)))
        samples = np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


class RealtimeStream:
    def __init__(self, cfg: dict):
        if not cfg.get("realtime_model"):
            raise AppError("MODEL_NOT_CONFIGURED", "No realtime model is configured",
                           hint="Enter the realtime model name (e.g. asr-realtime) in Settings › Connections")
        self.cfg = cfg
        self.text = ""
        self.final: str | None = None
        self.error: AppError | None = None
        self.state = "connecting"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._done = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, name="dictation-realtime", daemon=True)

    # ── called from the service ──
    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2)

    def feed(self, block: bytes, rate: int, channels: int) -> None:
        """Called from the recorder's writer thread (never the audio callback)."""
        if self.error or self._loop is None or self._queue is None:
            return
        try:
            pcm = to_pcm16_mono_16k(block, rate, channels)
        except Exception:
            return
        self._loop.call_soon_threadsafe(self._put, pcm)

    def finish(self, timeout: float = 20) -> str:
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(self._put, None)
        if not self._done.wait(timeout):
            self.close()
            raise AppError("REALTIME_UNAVAILABLE", "The realtime server did not finish in time")
        if self.error:
            raise self.error
        return (self.final if self.final is not None else self.text).strip()

    def close(self) -> None:
        if self._loop and not self._done.is_set():
            self._loop.call_soon_threadsafe(self._put, "close")

    # ── stream thread ──
    def _put(self, item) -> None:
        if self._queue is None:
            return
        if self._queue.qsize() >= MAX_QUEUE and item not in (None, "close"):
            self._fail(AppError("REALTIME_UNAVAILABLE", "The realtime server is not keeping up"))
            return
        self._queue.put_nowait(item)

    def _fail(self, error: AppError) -> None:
        if not self.error:
            self.error = error
        self.state = "failed"

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except AppError as exc:
            self._fail(exc)
        except Exception:
            self._fail(AppError("REALTIME_UNAVAILABLE", "Realtime transcription failed"))
        finally:
            self._done.set()
            self._ready.set()

    async def _run(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise AppError("REALTIME_UNAVAILABLE", "The websockets package is missing",
                           hint="Reinstall dictation4me (uv tool install --reinstall .)") from exc
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._ready.set()
        key = resolve(self.cfg)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            connection = websockets.connect(realtime_url(self.cfg), additional_headers=headers,
                                            max_size=4 * 1024 * 1024, open_timeout=8,
                                            proxy=None)
            async with connection as ws:
                first = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if first.get("type") != "session.created":
                    raise AppError("REALTIME_UNAVAILABLE", "Unexpected first message from the realtime server")
                await ws.send(json.dumps({"type": "session.update", "model": self.cfg["realtime_model"]}))
                await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                self.state = "live"
                reader = asyncio.create_task(self._read(ws))
                while True:
                    item = await self._queue.get()
                    if item == "close":
                        reader.cancel()
                        return
                    if item is None:
                        await ws.send(json.dumps({"type": "input_audio_buffer.commit", "final": True}))
                        self.state = "finishing"
                        break
                    await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                              "audio": base64.b64encode(item).decode("ascii")}))
                await asyncio.wait_for(reader, 30)
                self.state = "done"
        except AppError:
            raise
        except TimeoutError as exc:
            raise AppError("REALTIME_UNAVAILABLE", "The realtime server did not respond") from exc
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                raise AppError("PROVIDER_AUTH", "The realtime server rejected the API key") from exc
            if status == 404:
                raise AppError("REALTIME_UNAVAILABLE", "The server has no realtime endpoint at this URL") from exc
            raise AppError("REALTIME_UNAVAILABLE", "Cannot use the realtime server") from exc

    async def _read(self, ws) -> None:
        while True:
            message = json.loads(await ws.recv())
            kind = message.get("type")
            if kind == "transcription.delta":
                self.text += message.get("delta") or ""
            elif kind == "transcription.done":
                self.final = message.get("text") or self.text
                return
            elif kind == "error":
                detail = message.get("error") or {}
                text = detail.get("message") if isinstance(detail, dict) else None
                raise AppError("REALTIME_UNAVAILABLE",
                               "Realtime server error" + (f": {str(text)[:120]}" if text else ""))
