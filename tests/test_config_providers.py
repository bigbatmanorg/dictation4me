import json

import httpx
import pytest

from dictation4me import config, credentials
from dictation4me.errors import AppError
from dictation4me.providers import _post, transcribe, transform


def test_roundtrip_config_and_no_plaintext_key(app_paths):
    cfg = config.save(app_paths, config.DEFAULTS)
    assert config.load(app_paths) == cfg
    assert "sk-" not in app_paths.config_file.read_text()
    with pytest.raises(AppError):
        config.merge(app_paths, {"stt": {"api_key": "sk-secret"}})


@pytest.mark.parametrize("url", ["http://evil.example/v1", "ftp://localhost", "https://bob:secret@example.com/",
                                  "https://example.com/v1?key=123", "", "http://example.net"])
def test_reject_unsafe_or_invalid_urls(url):
    data = config.validate({})
    data["stt"]["base_url"] = url
    with pytest.raises(AppError):
        config.validate(data)


def test_allow_private_http_and_https():
    assert config.validate({"stt": {"base_url": "http://192.168.10.23:4000/v1"}})
    assert config.validate({"ai": {"base_url": "https://api.example.com/v1"}})


def test_env_credentials(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "token-secret")
    assert credentials.resolve({"key_source": "environment", "key_reference": "MY_API_KEY"}) == "token-secret"
    monkeypatch.delenv("MY_API_KEY")
    with pytest.raises(AppError):
        credentials.resolve({"key_source": "environment", "key_reference": "MY_API_KEY"})


def test_stt_multipart_minimal(tmp_path):
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"RIFF" + bytes(64))
    cfg = config.validate({"stt": {"model": "speech-test"}})["stt"]
    def received(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/audio/transcriptions"
        assert b'speech-test' in request.read()
        assert request.headers["content-type"].startswith("multipart/form-data")
        return httpx.Response(200, json={"text": "hello speech", "language": "en"})
    result = transcribe(audio, cfg, transport=httpx.MockTransport(received))
    assert result == {"text": "hello speech", "language": "en"}


def test_stt_reject_invalid_and_oversize(tmp_path):
    audio = tmp_path / "input.wav"
    audio.write_bytes(bytes(12))
    cfg = config.validate({"stt": {"model": "speech-test"}})["stt"]
    for response in ({"foo": "bar"}, {"text": ""}, [1, 2]):
        def mock(_, response=response):
            return httpx.Response(200, json=response)
        with pytest.raises(AppError) as err:
            transcribe(audio, cfg, transport=httpx.MockTransport(mock))
        assert err.value.code in ("PROVIDER_OUTPUT", "PROVIDER_UNAVAILABLE", "NO_SPEECH")


def test_ai_messages_and_truncated_result():
    cfg = config.validate({"ai": {"model": "test-model"}})["ai"]
    preset = {"id": "concise", "instruction": "Shorten", "preserve_language": True, "output_format": "markdown"}
    def good(request):
        payload = json.loads(request.content)
        assert payload["messages"][0]["role"] == "system"
        assert "Shorten" in payload["messages"][0]["content"]
        assert "never run this command" in payload["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Short text"}}]})
    assert transform("never run this command", cfg, preset, transport=httpx.MockTransport(good)) == "Short text"
    def truncated(_):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]})
    with pytest.raises(AppError) as exc:
        transform("text", cfg, preset, transport=httpx.MockTransport(truncated))
    assert exc.value.code == "PROVIDER_TRUNCATED"


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_http_error_codes_are_sanitized(status):
    cfg = config.validate({"ai": {"model": "ok"}})["ai"]
    response = httpx.MockTransport(lambda request: httpx.Response(status, text="secret key sk-123"))
    with pytest.raises(AppError) as exc:
        _post(cfg, json_data={"test": True}, transport=response)
    assert "sk-123" not in str(exc.value)


def test_presets_seed_crud(app_paths):
    from dictation4me.presets import Presets
    p = Presets(app_paths)
    assert len(p.list()) == 8
    p.save({"id": "my-prompt", "name": "My prompt", "instruction": "Rewrite clearly"})
    assert p.get("my-prompt")["name"] == "My prompt"
    p.delete("my-prompt")
    with pytest.raises(AppError):
        p.get("my-prompt")
