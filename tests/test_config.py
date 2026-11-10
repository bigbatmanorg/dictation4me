import os
import stat
import tomllib

import pytest
from dictation4me.config import Config, ConfigError, ServiceConfig, dumps, from_dict, load, parse_hotkey, save
from dictation4me import paths


@pytest.mark.parametrize("base,expected", [
    ("http://localhost:4000", "http://localhost:4000/v1/audio/transcriptions"),
    ("https://host/v1/", "https://host/v1/audio/transcriptions"),
    ("https://host/proxy", "https://host/proxy/v1/audio/transcriptions"),
    ("https://host/proxy/v1", "https://host/proxy/v1/audio/transcriptions"),
    ("https://host/v1/audio/transcriptions", "https://host/v1/audio/transcriptions"),
    ("http://[::1]:4000", "http://[::1]:4000/v1/audio/transcriptions"),
])
def test_endpoint(base, expected):
    assert ServiceConfig(base_url=base).endpoint() == expected


def test_endpoint_override():
    cfg = ServiceConfig(base_url="https://host/prefix", endpoint_path="/audio/transcriptions")
    assert cfg.endpoint() == "https://host/audio/transcriptions"


@pytest.mark.parametrize("url", ["ftp://host", "localhost:4000", "", "https://user:secret@host", "https://host?api_key=secret", "https://host/#foo", "http://[bad", "http://host:99999"])
def test_bad_urls(url):
    cfg = Config()
    cfg.service.base_url = url
    with pytest.raises(ConfigError):
        cfg.validate()


@pytest.mark.parametrize("section,field,value", [
    ("audio", "max_seconds", 0), ("audio", "max_seconds", 901),
    ("audio", "max_seconds", True), ("audio", "device", 12),
    ("service", "timeout_seconds", 4), ("service", "model", ""),
    ("service", "api_key", "a\nb"), ("service", "api_key_env", "x y"),
    ("output", "paste_shortcut", "enter"), ("output", "paste_delay_ms", -1),
    ("output", "backend", "shell"), ("service", "endpoint_path", "//attacker"),
])
def test_invalid_values(section, field, value):
    cfg = Config()
    setattr(getattr(cfg, section), field, value)
    with pytest.raises(ConfigError):
        cfg.validate()


def test_roundtrip_secret_unicode():
    cfg = Config()
    cfg.service.api_key = 'quote"backslash\\anděšč'
    save(cfg)
    assert load() == cfg
    assert from_dict(tomllib.loads(dumps(cfg))) == cfg
    if os.name != "nt":
        assert stat.S_IMODE(paths.config_file().stat().st_mode) == 0o600


def test_environment_precedence(monkeypatch):
    cfg = ServiceConfig(api_key="file-secret")
    monkeypatch.setenv("LITELLM_API_KEY", "env-secret")
    assert cfg.key() == "env-secret"
    monkeypatch.setenv("LITELLM_API_KEY", "")
    assert cfg.key() == "file-secret"


def test_unknown_and_malformed(tmp_path):
    with pytest.raises(ConfigError):
        from_dict({"transcription": {}})
    with pytest.raises(ConfigError):
        from_dict({"audio": {"typo": 1}})
    with pytest.raises(ConfigError):
        from_dict({"audio": "invalid"})
    file = tmp_path / "bad.toml"
    file.write_text('api_key = "SUPERSECRET\n')
    with pytest.raises(ConfigError) as error:
        load(file)
    assert "SUPERSECRET" not in str(error.value)


@pytest.mark.parametrize("value", ["d", "ctrl++d", "alt+F12", "ctrl+ctrl+d", "ctrl+Enter", "hyper+d"])
def test_bad_shortcuts(value):
    with pytest.raises(ConfigError):
        parse_hotkey(value)


def test_good_shortcuts():
    assert parse_hotkey("Meta+Shift+D") == ({"super", "shift"}, "d")
    assert parse_hotkey("Control + Alt + F11") == ({"ctrl", "alt"}, "f11")
