import io
import wave

import httpx
import numpy as np
import pytest

from dictation4me.audio import AudioError, encode_wav, resolve_device
from dictation4me.config import ServiceConfig
from dictation4me.stt import TranscriptionError, transcribe


def wav_bytes():
    return encode_wav(np.sin(np.arange(4800) * 0.05).astype(np.float32), 16000)


@pytest.mark.parametrize("rate", [16000, 44100, 48000, 96000])
def test_resample_and_header(rate):
    samples = np.ones((rate // 2, 2), dtype=np.float32) * 0.25
    data = encode_wav(samples, rate)
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert abs(wav.getnframes() - 8000) <= 1
        pcm = np.frombuffer(wav.readframes(8000), "<i2")
        assert 8000 < pcm[100] < 8400


def test_audio_clips_without_wraparound():
    data = encode_wav(np.ones(4000) * 3, 16000)
    assert np.frombuffer(data[44:], "<i2").max() == 32767


@pytest.mark.parametrize("samples,rate", [(np.array([]),16000), (np.ones(20),16000),
                                          (np.full(4000,np.nan),16000), (np.ones(4000),1)])
def test_bad_audio(samples, rate):
    with pytest.raises(AudioError):
        encode_wav(samples, rate)


def test_device_resolution():
    devices = [{"index": 2, "name": "USB headset"}, {"index": 3, "name": "USB mic"}]
    assert resolve_device("default", devices) is None
    assert resolve_device("headset", devices) == 2
    assert resolve_device("index:3", devices) == 3
    for name in ("USB", "missing", "index:99", "index:no"):
        with pytest.raises(AudioError):
            resolve_device(name, devices)


async def test_multipart_contract():
    wav = wav_bytes()
    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer secret"
        body = request.content
        assert b'name="model"' in body and b"whisper-1" in body
        assert b'name="language"' in body and b"\r\ncs\r\n" in body
        assert b'filename="dictation.wav"' in body and wav in body
        return httpx.Response(200, json={"text": "  Příliš žluťoučký kůň.  "})
    result = await transcribe(wav, ServiceConfig(api_key="secret", language="cs"),
                              transport=httpx.MockTransport(handler))
    assert result == "Příliš žluťoučký kůň."


async def test_auto_language_and_optional_auth():
    def handler(request):
        assert "authorization" not in request.headers
        assert b'name="language"' not in request.content
        return httpx.Response(200, json={"text": "ok"})
    assert await transcribe(wav_bytes(), ServiceConfig(), transport=httpx.MockTransport(handler)) == "ok"


@pytest.mark.parametrize("status", [301, 401, 403, 404, 413, 429, 500])
async def test_http_errors_do_not_leak_bodies(status):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="SECRET server echoed credentials"))
    with pytest.raises(TranscriptionError) as exc:
        await transcribe(wav_bytes(), ServiceConfig(), transport=transport)
    assert str(status) in str(exc.value)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("content", [b"bad JSON", b'{}', b'{"text":null}', b'{"text":3}', b'{"text":" "}', b'{"text":"bad\\u0000data"}'])
async def test_invalid_response(content):
    with pytest.raises(TranscriptionError):
        await transcribe(wav_bytes(), ServiceConfig(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)))


async def test_timeout_sanitized():
    def handler(request):
        raise httpx.ReadTimeout("SECRET raw detail", request=request)
    with pytest.raises(TranscriptionError) as error:
        await transcribe(wav_bytes(), ServiceConfig(), transport=httpx.MockTransport(handler))
    assert "SECRET" not in str(error.value)


async def test_reject_invalid_wav():
    with pytest.raises(TranscriptionError):
        await transcribe(b"bad", ServiceConfig())


def test_recorder_memory_limit_keeps_valid_audio():
    from dictation4me.audio import Recorder
    recorder = Recorder()
    recorder.rate = 16000
    recorder.limit = 4000
    recorder._callback(np.ones((5000, 1), dtype=np.float32) * 0.1, 5000, None, None)
    recorder._callback(np.ones((5000, 1), dtype=np.float32), 5000, None, None)
    assert recorder.frames == 4000 and recorder.limit_reached
    assert not recorder.error
    assert len(recorder.stop().samples) == 4000


def test_recorder_callback_overflow_is_reported():
    from dictation4me.audio import Recorder
    recorder = Recorder()
    recorder.rate = 16000
    recorder.limit = 10000
    recorder._callback(np.zeros((4000, 1), dtype=np.float32), 4000, None, "overflow")
    with pytest.raises(AudioError, match="overflow"):
        recorder.stop()


async def test_real_loopback_http_upload():
    """Exercise httpx's actual TCP transport, not MockTransport."""
    import asyncio
    import json

    received = asyncio.get_running_loop().create_future()
    wav = wav_bytes()

    async def serve(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            headers = dict(line.split(b":", 1) for line in head.split(b"\r\n")[1:] if b":" in line)
            size = int(next(value for key, value in headers.items() if key.lower() == b"content-length"))
            body = await asyncio.wait_for(reader.readexactly(size), 2)
            received.set_result((head, body))
            response = json.dumps({"text": "Network round trip — čeština"}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                         + f"Content-Length: {len(response)}\r\n\r\n".encode() + response)
            await writer.drain()
        except Exception as exc:
            if not received.done():
                received.set_exception(exc)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        result = await transcribe(wav, ServiceConfig(base_url=f"http://127.0.0.1:{port}/v1"))
        head, body = await asyncio.wait_for(received, 2)
        assert head.startswith(b"POST /v1/audio/transcriptions HTTP/1.1\r\n")
        assert wav in body
        assert result == "Network round trip — čeština"
