"""Credentials are only environment values or OS keyring entries, never TOML fields."""
from __future__ import annotations

import os

from .errors import AppError

SERVICE = "dictation4me"


def store_secret(name: str, value: str) -> None:
    if not name or not value:
        raise AppError("SECRET", "Credential name and value must be non-empty")
    try:
        import keyring
        keyring.set_password(SERVICE, name, value)
        if keyring.get_password(SERVICE, name) != value:
            raise RuntimeError("Keyring verification failed")
    except Exception as exc:
        raise AppError("KEYRING", "Secure OS keyring unavailable; use an environment variable instead") from exc


def resolve(provider: dict) -> str | None:
    source = provider["key_source"]
    name = provider["key_reference"]
    if source == "none":
        return None
    if source == "environment":
        value = os.environ.get(name, "")
        if not value:
            raise AppError("SECRET", f"The configured environment variable {name} is not available to the resident service")
        return value
    if source == "keyring":
        try:
            import keyring
            value = keyring.get_password(SERVICE, name)
        except Exception as exc:
            raise AppError("KEYRING", "Secure credential storage is unavailable") from exc
        if not value:
            raise AppError("SECRET", "No credential is saved under the configured keyring reference")
        return value
    raise AppError("SECRET", "Invalid credential source")
