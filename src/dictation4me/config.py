"""Strict, small TOML configuration and endpoint normalization."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit, urlunsplit

from . import paths


class ConfigError(ValueError):
    pass


@dataclass
class ServiceConfig:
    base_url: str = "http://localhost:4000/v1"
    model: str = "whisper-1"
    language: str = "auto"
    api_key_env: str = "LITELLM_API_KEY"
    api_key: str = ""
    timeout_seconds: int = 120
    # Non-standard gateways can supply a path relative to the origin.
    endpoint_path: str = ""

    def key(self) -> str:
        return (os.environ.get(self.api_key_env, "") if self.api_key_env else "") or self.api_key

    def endpoint(self) -> str:
        parsed = urlsplit(self.base_url.strip())
        path = parsed.path.rstrip("/")
        if self.endpoint_path:
            path = self.endpoint_path
        elif path.endswith("/audio/transcriptions"):
            pass
        elif path.endswith("/v1"):
            path += "/audio/transcriptions"
        else:
            path += "/v1/audio/transcriptions"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@dataclass
class AudioConfig:
    device: str = "default"
    max_seconds: int = 300
    max_buffer_mb: int = 128


@dataclass
class OutputConfig:
    auto_paste: bool = True
    paste_shortcut: str = "ctrl+v"
    paste_delay_ms: int = 250
    backend: str = "auto"  # auto, ydotool, xdotool, clipboard
    ydotool_socket: str = ""
    ydotool_autostart: bool = True


@dataclass
class DesktopConfig:
    hotkey: str = "ctrl+alt+d"
    hotkey_backend: str = "auto"  # auto, portal, x11, external, windows
    autostart: bool = True


@dataclass
class Config:
    service: ServiceConfig = field(default_factory=ServiceConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    desktop: DesktopConfig = field(default_factory=DesktopConfig)

    def validate(self) -> None:
        # Explicit types prevent TOML strings or bool-as-int values from reaching system APIs.
        defaults = Config()
        for section in fields(self):
            obj, default = getattr(self, section.name), getattr(defaults, section.name)
            for item in fields(obj):
                value, example = getattr(obj, item.name), getattr(default, item.name)
                if type(value) is not type(example):
                    raise ConfigError(f"{section.name}.{item.name}: expected {type(example).__name__}")
                if isinstance(value, str) and any(c in value for c in ("\0", "\r", "\n")):
                    raise ConfigError(f"{section.name}.{item.name}: control characters are not allowed")
        try:
            url = urlsplit(self.service.base_url)
            port = url.port
        except ValueError as exc:
            raise ConfigError("Invalid endpoint URL") from exc
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ConfigError("Base URL must be an absolute http:// or https:// URL")
        if url.username or url.password or url.query or url.fragment:
            raise ConfigError("Put the API key in its own field, not in URL credentials or a query")
        if port is not None and not 0 < port <= 65535:
            raise ConfigError("Invalid endpoint port")
        if self.service.endpoint_path and (
            not self.service.endpoint_path.startswith("/")
            or self.service.endpoint_path.startswith("//")
            or any(c in self.service.endpoint_path for c in "?#")
        ):
            raise ConfigError("endpoint_path must be an absolute path without a query or fragment")
        if not self.service.model.strip():
            raise ConfigError("An STT model alias is required")
        if self.service.api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.service.api_key_env):
            raise ConfigError("Invalid API-key environment variable name")
        if not 5 <= self.service.timeout_seconds <= 1200:
            raise ConfigError("Request timeout must be between 5 and 1200 seconds")
        if not 1 <= self.audio.max_seconds <= 900:
            raise ConfigError("Recording duration must be between 1 and 900 seconds")
        if not 8 <= self.audio.max_buffer_mb <= 512:
            raise ConfigError("Audio buffer limit must be between 8 and 512 MiB")
        if not 0 <= self.output.paste_delay_ms <= 5000:
            raise ConfigError("Paste delay must be between 0 and 5000 milliseconds")
        if self.output.paste_shortcut not in ("ctrl+v", "ctrl+shift+v", "shift+insert"):
            raise ConfigError("Paste shortcut must be ctrl+v, ctrl+shift+v or shift+insert")
        if self.output.backend not in ("auto", "ydotool", "xdotool", "clipboard"):
            raise ConfigError("Unsupported paste backend")
        if self.desktop.hotkey_backend not in ("auto", "portal", "x11", "external", "windows"):
            raise ConfigError("Unsupported shortcut backend")
        parse_hotkey(self.desktop.hotkey)


def parse_hotkey(value: str) -> tuple[set[str], str]:
    parts = value.lower().replace(" ", "").split("+")
    aliases = {"control": "ctrl", "win": "super", "meta": "super"}
    mods = [aliases.get(v, v) for v in parts[:-1]]
    key = parts[-1]
    valid_key = bool(re.fullmatch(r"[a-z0-9]|f(?:[1-9]|1[01])", key))
    if not valid_key or not mods or any(m not in {"ctrl", "alt", "shift", "super"} for m in mods):
        raise ConfigError("Use a shortcut such as ctrl+alt+d (letters, digits or F1–F11)")
    if len(set(mods)) != len(mods):
        raise ConfigError("Duplicate shortcut modifier")
    return set(mods), key


def from_dict(data: dict) -> Config:
    cfg = Config()
    allowed = {f.name for f in fields(cfg)}
    if set(data) - allowed:
        raise ConfigError("Unknown config section(s): " + ", ".join(sorted(set(data) - allowed)))
    for section, values in data.items():
        target = getattr(cfg, section)
        if not isinstance(values, dict):
            raise ConfigError(f"{section} must be a table")
        known = {f.name for f in fields(target)}
        if set(values) - known:
            raise ConfigError(f"Unknown option in {section}: " + ", ".join(sorted(set(values) - known)))
        for key, value in values.items():
            setattr(target, key, value)
    cfg.validate()
    return cfg


def load(path: Path | None = None, *, missing_ok: bool = False) -> Config:
    path = path or paths.config_file()
    if not path.exists():
        if missing_ok:
            return Config()
        raise ConfigError("Not configured yet. Run: dictation4me init")
    try:
        return from_dict(tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        # Do not include parser excerpts: a bad line may contain the secret.
        raise ConfigError(f"Cannot read {path.name}; check its TOML syntax and permissions") from exc


def dumps(cfg: Config) -> str:
    cfg.validate()
    lines = ["# Dictation4me — contains credentials when api_key is set.", ""]
    for section, values in asdict(cfg).items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines)


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or paths.config_file()
    paths.private_dir(path.parent)
    paths.atomic_write(path, dumps(cfg))
