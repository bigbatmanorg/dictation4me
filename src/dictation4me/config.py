"""Strict, secret-free configuration; only supported keys are persisted."""
from __future__ import annotations

import copy
import ipaddress
import json
import re
import socket
import tomllib
from urllib.parse import urlsplit

from .errors import AppError
from .paths import Paths, atomic_text

DEFAULTS = {
    "app": {"theme": "cobalt", "density": "compact", "autostart": False, "language": "auto"},
    "audio": {"device": "default", "max_seconds": 300, "max_spool_mb": 128,
              "sample_rate": 16000, "channels": 1, "retain_note_audio": True},
    "stt": {"base_url": "http://127.0.0.1:4000/v1", "path": "/audio/transcriptions",
            "model": "", "request_profile": "minimal", "key_source": "none",
            "key_reference": "", "timeout_seconds": 120, "max_file_mb": 25,
            "language_hint": "auto"},
    "ai": {"base_url": "http://127.0.0.1:4000/v1", "path": "/chat/completions",
           "model": "", "key_source": "none", "key_reference": "", "timeout_seconds": 120,
           "max_source_chars": 80000, "max_output_chars": 120000,
           "automatic_quick_preset_id": ""},
    "quick": {"shortcut": "Ctrl+Alt+D", "manual_delivery": True, "auto_deliver": False,
              "paste_to_current_focus_unsafe_wayland": False, "output_mode": "clipboard",
              "paste_shortcut": "Ctrl+V", "quick_text_retention_hours": 24,
              "record_audio_success_retention": "delete_after_transcription"},
    "privacy": {"confirm_remote_provider": True, "remote_provider_approved": False,
                "send_diagnostics": False, "log_content": False},
    "service": {"max_provider_jobs": 2, "max_ipc_message_bytes": 1048576, "request_poll_ms": 350},
}


LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa")


def _private_ip(value: str) -> bool | None:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    return ip.is_loopback or ip.is_private or ip.is_link_local


def is_local_host(host: str) -> bool:
    """Loopback, private/link-local IP, single-label LAN name, or a LAN DNS suffix.

    A dotted DNS name that resolves only to private addresses is also local
    (e.g. a split-horizon home DNS). This is a UX consent classifier, not an
    SSRF security boundary.
    """
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    literal = _private_ip(host)
    if literal is not None:
        return literal
    if host in ("localhost", "host.docker.internal") or "." not in host:
        return True
    if host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    addresses = {info[4][0].split("%", 1)[0] for info in infos}
    return bool(addresses) and all(_private_ip(a) for a in addresses)


def url_check(value: str) -> None:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise AppError("CONFIG", "Provider URL must be an HTTP(S) base URL without credentials")
    if parts.query or parts.fragment or len(value) > 2048:
        raise AppError("CONFIG", "Provider URL must not contain query parameters or fragments")
    if parts.scheme == "http" and not is_local_host(parts.hostname):
        raise AppError("CONFIG", "Plain HTTP is permitted only for a local/private endpoint; use HTTPS")


def is_remote_provider(url: str) -> bool:
    """Classify endpoint for consent; public DNS names and addresses are remote."""
    return not is_local_host(urlsplit(url).hostname or "")


MODIFIER_ALIASES = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
                    "meta": "Meta", "super": "Meta", "win": "Meta", "logo": "Meta"}
MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Meta")


def normalize_shortcut(value: str) -> str:
    """Canonical 'Ctrl+Alt+Shift+Meta+KEY'; Meta is the KDE name for Super/Windows."""
    parts = [p.strip() for p in str(value).replace(" ", "").split("+")]
    if len(parts) < 2 or not all(parts):
        raise AppError("CONFIG", "Shortcut must be modifiers and one letter/digit/F1-F12, e.g. Ctrl+Alt+D")
    mods = set()
    for part in parts[:-1]:
        canonical = MODIFIER_ALIASES.get(part.lower())
        if not canonical or canonical in mods:
            raise AppError("CONFIG", f"Unknown or repeated shortcut modifier: {part}")
        mods.add(canonical)
    key = parts[-1].upper()
    if not re.fullmatch(r"[A-Z0-9]|F(?:[1-9]|1[0-2])", key):
        raise AppError("CONFIG", "Shortcut key must be one letter, digit or F1-F12")
    if mods == {"Shift"}:
        raise AppError("CONFIG", "Shift alone is not a usable global shortcut modifier")
    return "+".join([m for m in MODIFIER_ORDER if m in mods] + [key])


def require_provider_consent(settings: dict, section: str) -> None:
    provider = settings[section]
    privacy = settings["privacy"]
    if (privacy["confirm_remote_provider"] and not privacy["remote_provider_approved"]
            and is_remote_provider(provider["base_url"])):
        raise AppError("CONSENT", "Remote provider selected. In Settings, approve sending audio/text to remote endpoints first")


def validate(config: dict) -> dict:
    if not isinstance(config, dict):
        raise AppError("CONFIG", "Configuration must be a table")
    clean = copy.deepcopy(DEFAULTS)
    for section, values in config.items():
        if section not in DEFAULTS or not isinstance(values, dict):
            raise AppError("CONFIG", f"Unknown configuration section: {section}")
        for key, value in values.items():
            if key not in DEFAULTS[section]:
                raise AppError("CONFIG", f"Unsupported configuration setting: {section}.{key}")
            default = DEFAULTS[section][key]
            if type(value) is not type(default):
                raise AppError("CONFIG", f"Wrong setting type: {section}.{key}")
            clean[section][key] = value
    for name in ("stt", "ai"):
        cfg = clean[name]
        url_check(cfg["base_url"])
        if not re.fullmatch(r"/[a-zA-Z0-9_./-]{1,100}", cfg["path"]) or ".." in cfg["path"]:
            raise AppError("CONFIG", f"Invalid {name} path")
        if len(cfg["model"]) > 200 or len(cfg["key_reference"]) > 150:
            raise AppError("CONFIG", "Model or credential reference is too long")
        if cfg["key_source"] not in ("none", "keyring", "environment"):
            raise AppError("CONFIG", f"Invalid {name} credential source")
        if cfg["key_source"] == "environment" and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", cfg["key_reference"]):
            raise AppError("CONFIG", f"{name} environment source requires a variable name")
        if cfg["key_source"] == "keyring" and not cfg["key_reference"]:
            raise AppError("CONFIG", f"{name} keyring source requires a reference")
        if not 1 <= cfg["timeout_seconds"] <= 600:
            raise AppError("CONFIG", "Provider timeout must be 1 to 600 seconds")
    if clean["stt"]["request_profile"] not in ("minimal", "openai-modern", "legacy-language"):
        raise AppError("CONFIG", "Invalid STT request profile")
    if not 1 <= clean["stt"]["max_file_mb"] <= 200:
        raise AppError("CONFIG", "Invalid STT size limit")
    if not 1 <= clean["audio"]["max_seconds"] <= 3600 or not 10 <= clean["audio"]["max_spool_mb"] <= 2048:
        raise AppError("CONFIG", "Audio recording limits are out of range")
    if clean["audio"]["sample_rate"] not in (16000, 24000, 44100, 48000) or clean["audio"]["channels"] not in (1, 2):
        raise AppError("CONFIG", "Unsupported audio sample rate or channel count")
    if clean["ai"]["max_source_chars"] < 10 or clean["ai"]["max_source_chars"] > 250000:
        raise AppError("CONFIG", "AI source size limit is out of range")
    if clean["ai"]["max_output_chars"] < 10 or clean["ai"]["max_output_chars"] > 500000:
        raise AppError("CONFIG", "AI output size limit is out of range")
    if clean["quick"]["output_mode"] not in ("clipboard", "attempt-paste"):
        raise AppError("CONFIG", "Invalid output mode")
    if clean["quick"]["paste_shortcut"].lower() not in ("ctrl+v", "ctrl+shift+v", "shift+insert"):
        raise AppError("CONFIG", "Unsupported paste shortcut")
    if clean["quick"]["record_audio_success_retention"] not in ("delete_after_transcription", "keep"):
        raise AppError("CONFIG", "Invalid quick recording retention")
    if clean["quick"]["quick_text_retention_hours"] not in (0, 24, 168, -1):
        raise AppError("CONFIG", "Quick text retention must be 0, 24, 168 or -1 hours")
    clean["quick"]["shortcut"] = normalize_shortcut(clean["quick"]["shortcut"])
    if clean["service"]["max_provider_jobs"] not in (1, 2, 3, 4):
        raise AppError("CONFIG", "Only 1-4 provider jobs are supported")
    if not 65536 <= clean["service"]["max_ipc_message_bytes"] <= 1048576:
        raise AppError("CONFIG", "IPC byte size must be 64 KiB to 1 MiB")
    return clean


def load(paths: Paths) -> dict:
    if not paths.config_file.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        data = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
        return validate(data)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise AppError("CONFIG", "Cannot read/validate config.toml: " + str(exc)) from exc


def dump_toml(data: dict) -> str:
    lines = ["# Dictation4me configuration (never contains API key values).", ""]
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, bool):
                encoded = "true" if value else "false"
            elif isinstance(value, str):
                encoded = json.dumps(value, ensure_ascii=False)
            else:
                encoded = str(value)
            lines.append(f"{key} = {encoded}")
        lines.append("")
    return "\n".join(lines)


def save(paths: Paths, config: dict) -> dict:
    clean = validate(config)
    atomic_text(paths.config_file, dump_toml(clean))
    return clean


def merge(paths: Paths, changes: dict) -> dict:
    current = load(paths)
    for section, values in changes.items():
        if section not in current or not isinstance(values, dict):
            raise AppError("CONFIG", "Invalid settings section")
        current[section].update(values)
    return save(paths, current)
