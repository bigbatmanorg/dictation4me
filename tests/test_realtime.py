"""Realtime streaming against a local fake of the vLLM/LiteLLM /v1/realtime protocol."""
import asyncio
import base64
import json
import threading
import time

import numpy as np
import pytest

from dictation4me import realtime
from dictation4me.errors import AppError


class FakeServer:
    def __init__(self, *, reject=False):
        self.reject, self.audio, self.headers, self.port = reject, b"", {}, None
        self.ready = threading.Event()
        self.loop = None

    async def handler(self, ws):
        self.headers = dict(ws.request.headers)
        await ws.send(json.dumps({"type": "session.created"}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "input_audio_buffer.append":
                self.audio += base64.b64decode(msg["audio"])
                await ws.send(json.dumps({"type": "transcription.delta", "delta": " word"}))
            elif msg["type"] == "input_audio_buffer.commit" and msg.get("final"):
                await ws.send(json.dumps({"type": "transcription.done", "text": " hello world"}))

    def run(self):
        import websockets

        async def main():
            def check(connection, request):
                if self.reject:
                    return connection.respond(401, "nope")
            async with websockets.serve(self.handler, "127.0.0.1", 0, process_request=check) as server:
                self.port = server.sockets[0].getsockname()[1]
                self.loop = asyncio.get_running_loop()
                self.stop = asyncio.Event()
                self.ready.set()
                await self.stop.wait()
        asyncio.run(main())

    def __enter__(self):
        threading.Thread(target=self.run, daemon=True).start()
        self.ready.wait(5)
        return self

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.stop.set)


def cfg(port, **extra):
    return {"base_url": f"http://127.0.0.1:{port}/v1", "realtime_model": "asr-realtime",
            "realtime_path": "/realtime", "key_source": "environment", "key_reference": "RT_KEY", **extra}


def test_url_mapping():
    assert realtime.realtime_url({"base_url": "https://gw.example/v1/", "realtime_model": "m",
                                  "realtime_path": "/realtime"}) == "wss://gw.example/v1/realtime?model=m"
    assert realtime.realtime_url(cfg(4000)).startswith("ws://127.0.0.1:4000/v1/realtime?model=")


def test_resample_48k_stereo_to_16k_mono():
    stereo = np.zeros((4800, 2), dtype=np.int16)
    stereo[:, 0], stereo[:, 1] = 1000, 3000
    out = np.frombuffer(realtime.to_pcm16_mono_16k(stereo.tobytes(), 48000, 2), dtype=np.int16)
    assert len(out) == 1600 and int(out.mean()) == 2000


def test_stream_live_text_and_final(monkeypatch):
    monkeypatch.setenv("RT_KEY", "secret-token")
    with FakeServer() as server:
        stream = realtime.RealtimeStream(cfg(server.port))
        stream.start()
        for _ in range(3):
            stream.feed(np.zeros(1600, dtype=np.int16).tobytes(), 16000, 1)
        deadline = time.monotonic() + 5
        while "word" not in stream.text and time.monotonic() < deadline:
            time.sleep(.02)
        assert "word" in stream.text  # live deltas arrive while recording
        assert stream.finish(timeout=5) == "hello world"
        assert len(server.audio) == 3 * 3200
        assert server.headers.get("authorization") == "Bearer secret-token"


def test_stream_auth_failure_is_actionable(monkeypatch):
    monkeypatch.setenv("RT_KEY", "bad")
    with FakeServer(reject=True) as server:
        stream = realtime.RealtimeStream(cfg(server.port))
        stream.start()
        with pytest.raises(AppError) as info:
            stream.finish(timeout=5)
        assert info.value.code == "PROVIDER_AUTH" and "key" in info.value.hint.lower()


def test_missing_realtime_model_explains_fix():
    with pytest.raises(AppError) as info:
        realtime.RealtimeStream({"realtime_model": ""})
    assert "realtime model" in info.value.hint.lower()
