"""One batch upload, with bounded errors and no credential/body logging."""
from __future__ import annotations

import asyncio
import json

import httpx
from .config import ServiceConfig


class TranscriptionError(RuntimeError):
    pass


async def transcribe(wav: bytes, config: ServiceConfig, *, transport=None) -> str:
    if len(wav) <= 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise TranscriptionError("No valid WAV recording to transcribe")
    if len(wav) > 24 * 1024 * 1024:
        raise TranscriptionError("Recording exceeds the 24 MiB upload limit; record a shorter message")
    fields = {"model": config.model, "response_format": "json"}
    if config.language.strip().lower() not in ("", "auto"):
        fields["language"] = config.language.strip()
    key = config.key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    timeout = httpx.Timeout(config.timeout_seconds, connect=min(15, config.timeout_seconds))
    try:
        # trust_env=False prevents an unrelated HTTP_PROXY from intercepting a LAN speech endpoint.
        # TLS verification remains enabled. Redirects are not followed with credentials.
        async with httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False,
                                    follow_redirects=False) as client:
            async with asyncio.timeout(config.timeout_seconds):
                async with client.stream("POST", config.endpoint(), data=fields,
                                         files={"file": ("dictation.wav", wav, "audio/wav")},
                                         headers=headers) as response:
                    status = response.status_code
                    if status != 200:
                        hints = {401: "Check your API key.", 403: "The API key is not authorized.",
                                 404: "Check the endpoint path and model alias.",
                                 413: "Record a shorter message.", 429: "Rate limited; retry later."}
                        raise TranscriptionError(f"Speech endpoint returned HTTP {status}. "
                                                 + hints.get(status, "Check the speech server."))
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise TranscriptionError("Speech response exceeded 1 MiB")
        data = json.loads(body)
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise TranscriptionError("Speech server returned no non-empty 'text' string")
        if "\0" in text:
            raise TranscriptionError("Speech response contained a NUL character")
        return text.strip()
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise TranscriptionError("Speech request timed out; recording is available for Retry") from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError("Cannot reach speech endpoint; check URL, network and TLS certificate") from exc
    except (ValueError, UnicodeError) as exc:
        raise TranscriptionError("Speech server returned invalid JSON") from exc
