"""Disk-spooled microphone recording; PortAudio callback never performs disk I/O."""
from __future__ import annotations

import hashlib
import os
import queue
import struct
import threading
import time
import wave
from pathlib import Path

from .errors import AppError
from .paths import Paths


class AudioRecorder:
    def __init__(self, paths: Paths, recording_id: str, settings: dict):
        self.paths = paths
        self.recording_id = recording_id
        self.settings = settings
        self.part = paths.spool / (recording_id + ".part")
        self.final = paths.audio / (recording_id + ".wav")
        self.blocks = queue.Queue(maxsize=32)
        self.error: str | None = None
        self._stream = None
        self._writer = None
        self._stopped = False
        self._bytes = 0
        self._start = 0.0
        self._max = int(settings["max_spool_mb"]) * 1024 * 1024
        self.rate = int(settings["sample_rate"])
        self.channels = int(settings["channels"])
        self.overflows = 0
        # Optional consumer of written PCM blocks (realtime transcription).
        # Called from the writer thread, never from the PortAudio callback.
        self.tap = None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            # sounddevice raises OSError when the PortAudio shared library is missing.
            raise AppError("AUDIO_UNAVAILABLE",
                           "PortAudio is unavailable; install libportaudio2 (Linux)") from exc
        self.paths.init()
        if self.part.exists():
            raise AppError("AUDIO_FILE_EXISTS", "A recording spool already exists")

        def callback(indata, frames, timestamp, status):
            if self.error:
                raise sd.CallbackStop
            if status and getattr(status, "input_overflow", False):
                # A host-side overrun (common once at ALSA stream start) drops a
                # few frames; keep recording. Only our own full queue is fatal.
                self.overflows += 1
            if time.monotonic() - self._start > self.settings["max_seconds"]:
                self.error = "AUDIO_MAX_DURATION"
                raise sd.CallbackStop
            try:
                self.blocks.put_nowait(indata.copy().tobytes())
            except queue.Full:
                self.error = "AUDIO_OVERFLOW"
                raise sd.CallbackStop from None

        device = self.settings["device"]
        # The UI select serializes PortAudio's numeric device IDs as strings.
        # A numeric string passed directly to sounddevice may be interpreted
        # as a device-name substring rather than the intended device index.
        chosen = None if device == "default" else (int(device) if str(device).isdigit() else device)
        stream, failure = None, None
        for sampling, channels in self._formats(sd, chosen):
            try:
                stream = sd.InputStream(samplerate=sampling, channels=channels, dtype="int16",
                                        device=chosen, blocksize=2048, callback=callback)
                break
            except Exception as exc:
                # Raw ALSA hw: devices commonly reject 16 kHz/mono; the WAV is
                # then recorded at the device's native format instead.
                failure = exc
        if stream is None:
            raise AppError("AUDIO_OPEN_FAILED",
                           "Cannot open microphone; check device and desktop permissions") from failure
        self.rate, self.channels = sampling, channels
        try:
            descriptor = os.open(self.part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            stream.close()
            raise
        handle = os.fdopen(descriptor, "wb")

        def writer() -> None:
            try:
                with handle:
                    with wave.open(handle, "wb") as wav:
                        wav.setnchannels(channels)
                        wav.setsampwidth(2)
                        wav.setframerate(sampling)
                        last_sync = time.monotonic()
                        while True:
                            block = self.blocks.get()
                            if block is None:
                                break
                            if self._bytes + len(block) > self._max:
                                self.error = "AUDIO_TOO_LARGE"
                                continue
                            wav.writeframesraw(block)
                            self._bytes += len(block)
                            if self.tap:
                                try:
                                    self.tap(block, sampling, channels)
                                except Exception:
                                    self.tap = None  # live text is optional; the WAV is not
                            # Every second, flush the audio blocks even though
                            # the WAV length fields are finalized only on stop.
                            # repair_spool() can recover a crash-interrupted file.
                            if time.monotonic() - last_sync >= 1.0:
                                handle.flush()
                                os.fsync(handle.fileno())
                                last_sync = time.monotonic()
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                self.error = "AUDIO_WRITE_ERROR"

        self._writer = threading.Thread(target=writer, name="dictation-audio-writer", daemon=True)
        self._writer.start()
        self._stream = stream
        try:
            self._start = time.monotonic()
            self._stream.start()
        except Exception as exc:
            self.error = "AUDIO_OPEN_FAILED"
            self.stop()
            raise AppError("AUDIO_OPEN_FAILED", "Cannot open microphone; check device and desktop permissions") from exc

    def _formats(self, sd, device) -> list[tuple[int, int]]:
        wanted = (int(self.settings["sample_rate"]), int(self.settings["channels"]))
        options = [wanted]
        try:
            info = sd.query_devices(device, "input")
            native = int(info["default_samplerate"])
            max_channels = max(1, min(2, int(info["max_input_channels"])))
            options += [(native, wanted[1]), (native, max_channels), (wanted[0], max_channels)]
        except Exception:
            pass
        return list(dict.fromkeys(options))

    def stop(self) -> dict:
        if self._stopped:
            raise AppError("AUDIO", "Recording is already finalized")
        self._stopped = True
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                self.error = self.error or "AUDIO_CLOSE_FAILED"
        # callback is quiesced; bounded queue will drain before sentinel is written
        if self._writer:
            try:
                self.blocks.put(None, timeout=5)
            except queue.Full:
                # Writer may be wedged on slow storage. Leave the .part file
                # for explicit recovery, never rename it while still written.
                self.error = self.error or "AUDIO_WRITE_TIMEOUT"
            self._writer.join(timeout=10)
            if self._writer.is_alive():
                self.error = "AUDIO_WRITE_TIMEOUT"
        if not self.part.exists():
            return {"file_relpath": None, "duration_ms": 0, "size_bytes": 0,
                    "error_code": self.error or "AUDIO_MISSING"}
        if self._writer and self._writer.is_alive():
            # Preserve the spool for a recovery attempt; never rename an active writer's file.
            return {"file_relpath": None, "duration_ms": 0, "size_bytes": self.part.stat().st_size,
                    "error_code": self.error}
        if self._bytes == 0:
            return {"file_relpath": None, "duration_ms": 0, "size_bytes": self.part.stat().st_size,
                    "error_code": self.error or "AUDIO_EMPTY"}
        os.replace(self.part, self.final)
        data = self.final.read_bytes()
        duration = int(self._bytes / (2 * self.channels * self.rate) * 1000)
        return {"file_relpath": "audio/" + self.final.name, "duration_ms": duration,
                "size_bytes": len(data), "content_sha256": hashlib.sha256(data).hexdigest(),
                "error_code": self.error}


def repair_spool(part: Path) -> dict:
    """Best-effort repair of crash-interrupted normal PCM16 WAV headers; never delete input."""
    with part.open("r+b") as stream:
        header = stream.read(44)
        size = part.stat().st_size
        if size < 46 or header[:4] != b"RIFF" or header[8:12] != b"WAVE" or header[36:40] != b"data":
            raise AppError("AUDIO_INCOMPLETE", "Incomplete audio cannot be automatically repaired")
        stream.seek(4)
        stream.write(struct.pack("<I", size - 8))
        stream.seek(40)
        stream.write(struct.pack("<I", size - 44))
        stream.flush()
        os.fsync(stream.fileno())
    with wave.open(str(part), "rb") as wav:
        return {"frames": wav.getnframes(), "rate": wav.getframerate(),
                "channels": wav.getnchannels()}


def devices() -> list[dict]:
    try:
        import sounddevice as sd
        return [{"id": i, "name": d["name"], "channels": d["max_input_channels"],
                 "default_samplerate": d["default_samplerate"]}
                for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    except Exception as exc:
        raise AppError("AUDIO_UNAVAILABLE", "Audio devices could not be enumerated") from exc


def play(path: Path) -> None:
    try:
        import numpy as np
        import sounddevice as sd
        with wave.open(str(path), "rb") as wav:
            if wav.getsampwidth() != 2:
                raise AppError("AUDIO_FORMAT", "Only PCM16 WAV playback is currently supported")
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).copy()
            samples = samples.reshape((-1, wav.getnchannels()))
            sd.play(samples, samplerate=wav.getframerate(), blocking=True)
    except AppError:
        raise
    except Exception as exc:
        raise AppError("AUDIO_PLAYBACK", "Audio playback failed on the selected output device") from exc
