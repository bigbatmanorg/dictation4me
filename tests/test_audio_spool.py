"""Exercise the real WAV spool/finalization path with a virtual microphone callback."""
from __future__ import annotations

import sys
import types
import wave

import numpy as np

from dictation4me.audio import AudioRecorder, repair_spool


def test_microphone_spool_and_wav_finalize(app_paths, monkeypatch):
    class FakeInputStream:
        def __init__(self, *, samplerate, channels, dtype, device, blocksize, callback):
            assert samplerate == 16000
            assert channels == 1
            assert device == 3  # numeric UI device ID becomes an integer
            self.callback = callback
        def start(self):
            sample = np.arange(4096, dtype=np.int16).reshape((-1, 1))
            self.callback(sample, 4096, None, None)
        def stop(self):
            pass
        def close(self):
            pass

    fake = types.SimpleNamespace(InputStream=FakeInputStream, CallbackStop=RuntimeError)
    monkeypatch.setitem(sys.modules, 'sounddevice', fake)
    cfg = {'sample_rate': 16000, 'channels': 1, 'device': '3',
           'max_seconds': 30, 'max_spool_mb': 10}
    recorder = AudioRecorder(app_paths, 'test-virtual-capture', cfg)
    recorder.start()
    result = recorder.stop()
    assert result['error_code'] is None
    assert result['duration_ms'] == 256
    assert result['size_bytes'] > 4096 * 2
    with wave.open(str(app_paths.media_path(result['file_relpath'])), 'rb') as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() == 4096
    assert not recorder.part.exists()


def test_salvage_crash_interrupted_wave_header(app_paths):
    part = app_paths.spool / 'incomplete.part'
    with wave.open(str(part), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\x00\x01' * 200)
    data = bytearray(part.read_bytes())
    data[4:8] = (0).to_bytes(4, 'little')
    data[40:44] = (0).to_bytes(4, 'little')
    part.write_bytes(data)
    result = repair_spool(part)
    assert result['frames'] == 200
