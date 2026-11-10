"""Native-rate microphone capture, bounded memory, mono/16 kHz WAV conversion."""
from __future__ import annotations

from dataclasses import dataclass
import io
import threading
import wave

import numpy as np
from .config import AudioConfig


class AudioError(RuntimeError):
    pass


def _sd():
    try:
        import sounddevice
        return sounddevice
    except (ImportError, OSError) as exc:
        raise AudioError("PortAudio unavailable. Run dictation4me doctor / setup-system") from exc


def list_devices() -> list[dict]:
    sd = _sd()
    try:
        devices, apis = sd.query_devices(), sd.query_hostapis()
        default = sd.default.device[0]
        return [{"index": i, "name": d["name"], "host_api": apis[d["hostapi"]]["name"],
                 "channels": d["max_input_channels"], "sample_rate": d["default_samplerate"],
                 "default": i == default}
                for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    except Exception as exc:
        raise AudioError("Cannot enumerate microphones; check your audio session") from exc


def resolve_device(wanted: str, devices: list[dict]) -> int | None:
    if wanted.strip().lower() in ("", "default"):
        return None
    if wanted.startswith("index:"):
        try:
            index = int(wanted[6:])
        except ValueError as exc:
            raise AudioError("Invalid microphone index") from exc
        if any(d["index"] == index for d in devices):
            return index
        raise AudioError("Configured microphone index no longer exists")
    exact = [d for d in devices if d["name"].casefold() == wanted.casefold()]
    matches = exact or [d for d in devices if wanted.casefold() in d["name"].casefold()]
    if len(matches) != 1:
        raise AudioError("Microphone name is missing or ambiguous; select it again in Settings")
    return matches[0]["index"]


def encode_wav(samples: np.ndarray, rate: int) -> bytes:
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if data.ndim != 1 or data.size == 0 or not 8000 <= rate <= 192000:
        raise AudioError("Invalid or empty audio recording")
    if not np.isfinite(data).all():
        raise AudioError("Audio contained non-finite samples")
    if len(data) < int(rate * 0.15):
        raise AudioError("Recording was too short (minimum 150 ms)")
    if rate != 16000:
        import soxr
        data = soxr.resample(data, rate, 16000, quality="HQ")
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm.tobytes())
    return stream.getvalue()


@dataclass
class Recording:
    samples: np.ndarray
    rate: int

    def wav(self) -> bytes:
        return encode_wav(self.samples, self.rate)


class Recorder:
    def __init__(self) -> None:
        self.stream = None
        self.chunks: list[np.ndarray] = []
        self.lock = threading.Lock()
        self.error = ""
        self.rate = 0
        self.frames = 0
        self.limit = 0
        self.level = 0.0
        self.limit_reached = False

    def start(self, config: AudioConfig) -> None:
        sd = _sd()
        self.abort()
        device = resolve_device(config.device, list_devices())
        try:
            info = sd.query_devices(device, "input")
            self.rate = int(info["default_samplerate"])
            if not 8000 <= self.rate <= 192000:
                raise AudioError("Unsupported native microphone sample rate")
            channels = 1
            try:
                sd.check_input_settings(device=device, channels=1, dtype="float32", samplerate=self.rate)
            except Exception:
                channels = min(2, info["max_input_channels"])
                sd.check_input_settings(device=device, channels=channels, dtype="float32", samplerate=self.rate)
            self.limit = min(self.rate * config.max_seconds,
                             config.max_buffer_mb * 1024 * 1024 // (4 * channels))
            self.error, self.frames = "", 0
            self.limit_reached = False
            self.stream = sd.InputStream(device=device, samplerate=self.rate, channels=channels,
                                         dtype="float32", blocksize=0, callback=self._callback)
            self.stream.start()
        except Exception as exc:
            self.abort()
            if isinstance(exc, AudioError):
                raise
            raise AudioError("Cannot open microphone. Check device selection and microphone permissions") from exc

    def _callback(self, data, frames, time_info, status) -> None:
        # No HTTP, disk writes, UI calls or blocking operations in PortAudio's callback.
        if status:
            self.error = "Audio capture overflow/underflow; please retry"
        with self.lock:
            remaining = self.limit - self.frames
            if remaining <= 0:
                self.limit_reached = True
                return
            chunk = data[:remaining].copy()
            self.frames += len(chunk)
            self.limit_reached = self.frames >= self.limit
            self.chunks.append(chunk)
            self.level = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0

    def stop(self) -> Recording:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self.lock:
            chunks, self.chunks = self.chunks, []
        self.level = 0.0
        if self.error:
            raise AudioError(self.error)
        if not chunks:
            raise AudioError("No audio was captured")
        return Recording(np.concatenate(chunks, axis=0), self.rate)

    def abort(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()
        with self.lock:
            self.chunks.clear()
        self.level = 0.0
