import hashlib
import threading
import time
import wave

import pytest

from dictation4me.engine import Engine
from dictation4me.errors import AppError, Conflict
from dictation4me.storage import digest


class FakeRecorder:
    def __init__(self, paths, ident, settings):
        self.paths, self.ident, self.settings = paths, ident, settings
        self.path = paths.audio / (ident + ".wav")

    def start(self):
        with wave.open(str(self.path), "wb") as stream:
            stream.setframerate(16000)
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.writeframes(bytes(16000))

    def stop(self):
        raw = self.path.read_bytes()
        return {"file_relpath": "audio/" + self.path.name, "duration_ms": 500,
                "size_bytes": len(raw), "content_sha256": hashlib.sha256(raw).hexdigest()}


class FakeDesktop:
    def __init__(self):
        self.content = ""
        self.paste_calls = 0
        self.foreground = "normal-window"
        self.read_override = None

    def foreground_token(self):
        return self.foreground

    def copy(self, value):
        self.content = value

    def read(self):
        return self.content if self.read_override is None else self.read_override

    def paste(self, shortcut, **kwargs):
        self.paste_calls += 1
        return "sent"


@pytest.fixture
def make_engine(app_paths, monkeypatch):
    import dictation4me.desktop
    monkeypatch.setattr(dictation4me.desktop, "notify", lambda msg: None)
    created = []
    def build(*, stt=None, ai=None, backend=None):
        backend = backend or FakeDesktop()
        engine = Engine(app_paths, recorder_type=FakeRecorder,
                        stt=stt or (lambda path, cfg: {"text": "Spoken text", "language": "en"}),
                        ai=ai or (lambda source, cfg, preset: "AI generated text"),
                        clipboard_factory=lambda: backend)
        created.append(engine)
        return engine, backend
    yield build
    for engine in reversed(created):
        try:
            engine.close()
        except Exception:
            pass


def ready(engine, expected="READY"):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        if engine.status()["state"] == expected:
            return
        time.sleep(.02)
    raise AssertionError(f"Expected {expected}; received {engine.status()}")


def press(engine):
    engine._last_hotkey = 0  # Simulate independent physical presses past debounce.
    return engine.hotkey()


def test_hotkey_quick_lifecycle_and_delivery(make_engine):
    engine, clipboard = make_engine()
    first = press(engine)
    assert first["state"] == "RECORDING"
    assert press(engine)["state"] == "TRANSCRIBING"
    ready(engine)
    assert engine.store.quick()["edited_text"] == "Spoken text"
    third = press(engine)
    assert third["paste_status"] == "not_attempted"  # safe clipboard default
    assert clipboard.content == "Spoken text"
    assert engine.status()["state"] == "RECOVERABLE_RESULT"
    assert press(engine)["state"] == "RECORDING"


def test_quick_edited_text_precedes_delivery(make_engine):
    engine, cb = make_engine()
    engine.start_capture("quick")
    engine.stop_capture()
    ready(engine)
    q = engine.store.quick()
    engine.handle("quick.edit", {"id": q["id"], "text": "My edited result"})
    engine.deliver_quick()
    assert cb.content == "My edited result"
    assert engine.store.quick()["original_text"] == "Spoken text"


def test_repeated_delivery_rejected_but_explicit_retry_allowed(make_engine):
    engine, cb = make_engine()
    engine.start_capture("quick")
    engine.stop_capture()
    ready(engine)
    engine.deliver_quick()
    with pytest.raises(AppError) as exc:
        engine.deliver_quick()
    assert exc.value.code == "DELIVERED"
    retry = engine.deliver_quick(allow_repeat=True)
    assert retry["clipboard_status"] == "copied"
    assert len(engine.store.recent_deliveries(engine.store.quick()["id"])) == 2


def test_clipboard_race_prevents_paste(make_engine):
    engine, cb = make_engine()
    engine.settings["quick"]["output_mode"] = "attempt-paste"
    engine.start_capture("quick")
    engine.stop_capture()
    ready(engine)
    cb.read_override = "foreign clipboard text"
    with pytest.raises(AppError) as exc:
        engine.deliver_quick()
    assert exc.value.code == "CLIPBOARD_CHANGED"
    assert cb.paste_calls == 0
    assert engine.status()["state"] == "READY"


def test_note_capture_autoinserts_without_replacing_existing_text(make_engine):
    engine, _ = make_engine()
    n = engine.store.create_note("Voice", "Initial text")
    engine.start_capture("note", n["id"])
    engine.stop_capture()
    ready(engine, "IDLE")
    assert engine.store.get_note(n["id"])["content_md"] == "Initial text\n\nSpoken text"
    rec = engine.store.recordings(note_id=n["id"])[0]
    assert engine.store.append_transcript(rec["id"])["already_applied"]


def test_note_edit_during_stt_wins(make_engine):
    hold = threading.Event()
    def delayed(path, cfg):
        hold.wait(3)
        return {"text": "Dictated", "language": "en"}
    engine, _ = make_engine(stt=delayed)
    n = engine.store.create_note("Voice", "Initial")
    engine.start_capture("note", n["id"])
    engine.stop_capture()
    engine.store.save_draft(n["id"], "Manual update", 0)
    hold.set()
    ready(engine, "IDLE")
    assert engine.store.get_note(n["id"])["content_md"] == "Manual update"
    rec = engine.store.recordings(note_id=n["id"])[0]
    assert rec["original_text"] == "Dictated"
    assert engine.store.append_transcript(rec["id"])["applied"]
    assert engine.store.get_note(n["id"])["content_md"].endswith("Dictated")


def test_cancel_discards_late_result_without_losing_audio(make_engine):
    entered = threading.Event()
    hold = threading.Event()
    def delayed(path, cfg):
        entered.set()
        hold.wait(3)
        return {"text": "Stale output", "language": "en"}
    engine, _ = make_engine(stt=delayed)
    first = engine.start_capture("quick")
    engine.stop_capture()
    assert entered.wait(2)
    engine.cancel()
    hold.set()
    time.sleep(.15)
    q = engine.store.quick(first["quick_id"])
    assert q["status"] == "cancelled"
    assert not q["transcript_id"]
    assert engine.store.recording(first["recording_id"])["file_relpath"]


def test_provider_failure_retains_audio_and_explicit_retry(make_engine):
    calls = [0]
    def failing_once(path, cfg):
        calls[0] += 1
        if calls[0] == 1:
            raise AppError("PROVIDER_UNAVAILABLE", "Provider offline")
        return {"text": "Recovered", "language": "en"}
    engine, _ = make_engine(stt=failing_once)
    first = engine.start_capture("quick")
    engine.stop_capture()
    ready(engine, "ERROR")
    assert engine.store.recording(first["recording_id"])["file_relpath"]
    with pytest.raises(AppError):
        engine.start_capture("quick")
    engine.retry_recording(first["recording_id"])
    ready(engine, "READY")
    assert engine.store.quick()["edited_text"] == "Recovered"


def test_ai_job_result_and_manual_edit_conflict(make_engine):
    engine, _ = make_engine()
    note = engine.store.create_note("A", "Initial")
    req = engine.transform_note(note["id"], "make-concise")
    end = time.monotonic()+3
    while time.monotonic()<end:
        job = engine.get_job_result(req["job_id"])
        if job["job"]["status"] == "succeeded":
            break
        time.sleep(.02)
    assert job["proposal"]["proposed_content_md"] == "AI generated text"
    engine.store.save_draft(note["id"], "Updated locally", 0)
    with pytest.raises(Conflict):
        engine.store.apply_proposal(job["proposal"]["id"], "current")


def test_ai_auto_quick_does_not_overwrite_manual_edits(make_engine):
    entered, proceed = threading.Event(), threading.Event()
    def delayed(source, cfg, preset):
        entered.set()
        proceed.wait(3)
        return "AI output"
    engine, _ = make_engine(ai=delayed)
    engine.settings["ai"]["automatic_quick_preset_id"] = "make-concise"
    engine.start_capture("quick")
    engine.stop_capture()
    assert entered.wait(2)
    q = engine.store.quick()
    engine.store.edit_quick(q["id"], "User edited before AI finished")
    proceed.set()
    ready(engine)
    assert engine.store.quick()["edited_text"] == "User edited before AI finished"


def test_crash_unknown_paste_not_replayed_on_next_press(make_engine, app_paths):
    engine, _ = make_engine()
    cap = engine.store.capture_started("quick")
    engine.store.capture_finalized(cap["recording_id"],
                                   {"file_relpath": "audio/a.wav"}, status="ready")
    engine.store.transcript_saved(cap["recording_id"], "Saved", "m")
    engine.store.delivery(cap["quick_id"], "attempt-1", digest("Saved"), "copied", "unknown")
    engine.close()
    next_engine, cb = make_engine()
    assert next_engine.status()["state"] == "RECOVERABLE_RESULT"
    assert press(next_engine)["state"] == "RECORDING"
    assert cb.paste_calls == 0


def test_delivery_intent_is_persisted_before_clipboard_and_not_replayed(make_engine):
    engine, cb = make_engine()
    engine.start_capture('quick')
    engine.stop_capture()
    ready(engine)
    item_id = engine.store.quick()['id']
    original_copy = cb.copy
    calls = [0]

    def failing_copy(text):
        calls[0] += 1
        assert len(engine.store.recent_deliveries(item_id)) == 1
        raise AppError('CLIPBOARD_BUSY', 'Clipboard occupied')

    cb.copy = failing_copy
    with pytest.raises(AppError):
        engine.deliver_quick(attempt_id='persisted-intent')
    first = engine.store.recent_deliveries(item_id)[0]
    assert first['clipboard_status'] == 'failed'
    assert first['paste_status'] == 'not_attempted'
    assert engine.deliver_quick(attempt_id='persisted-intent')['clipboard_status'] == 'failed'
    assert calls == [1]
    cb.copy = original_copy
    assert engine.deliver_quick(attempt_id='new-explicit-intent')['clipboard_status'] == 'copied'


def test_quick_ai_proposal_is_bound_to_correct_capture(make_engine):
    engine, _ = make_engine()
    for _ in range(2):
        engine.start_capture('quick')
        engine.stop_capture()
        ready(engine)
        if _ == 0:
            first = engine.store.quick()['id']
        else:
            second = engine.store.quick()['id']
    ai_job = engine.transform_quick(first, 'make-concise')
    end = time.monotonic() + 3
    while time.monotonic() < end:
        result = engine.get_job_result(ai_job['job_id'])
        if result['job']['status'] == 'succeeded':
            break
        time.sleep(.01)
    proposal = result['proposal']['id']
    with pytest.raises(Conflict):
        engine.accept_quick_proposal(second, proposal)
    engine.accept_quick_proposal(first, proposal)
    assert engine.store.proposal(proposal)['status'] == 'accepted'


def test_remote_provider_explicit_approval(make_engine):
    engine, _ = make_engine()
    note = engine.store.create_note('Remote check', 'Private content')
    assert engine.handle('config.save', {'changes': {'ai': {
        'base_url': 'https://api.example.org/v1', 'model': 'my-model'}}})
    with pytest.raises(AppError) as exc:
        engine.transform_note(note['id'], 'make-concise')
    assert exc.value.code == 'CONSENT'
    engine.handle('config.save', {'changes': {'privacy': {'remote_provider_approved': True}}})
    submitted = engine.transform_note(note['id'], 'make-concise')
    end = time.monotonic()+2
    while time.monotonic()<end:
        result = engine.get_job_result(submitted['job_id'])
        if result['job']['status'] == 'succeeded':
            break
        time.sleep(.01)
    assert result['job']['status'] == 'succeeded'


def test_shortcut_change_rebinds_live(make_engine, monkeypatch):
    import dictation4me.desktop
    bound = []

    class FakeHotkey:
        def __init__(self, value, callback):
            self.value, self.closed, self.description = value, False, value
            bound.append(self)

        def start(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(dictation4me.desktop, "shortcut", FakeHotkey)
    engine, _ = make_engine()
    engine.start_hotkey()
    result = engine.save_settings({"quick": {"shortcut": "meta+d"}})
    assert result["shortcut_changed"] and not result["restart_required"]
    assert [h.value for h in bound] == ["Ctrl+Alt+D", "Meta+D"]
    assert bound[0].closed and not bound[1].closed
    assert "Meta+D" in result["hotkey"]
    # Saving unrelated settings must not churn the registration.
    engine.save_settings({"ai": {"model": "brain-direct"}})
    assert len(bound) == 2


def test_hotkey_status_explains_desktop_owned_binding(make_engine, monkeypatch):
    import dictation4me.desktop

    class Bound:
        def __init__(self, value, callback):
            self.error, self.description = None, "Meta+L"

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(dictation4me.desktop, "shortcut", Bound)
    monkeypatch.setattr(dictation4me.desktop, "session_type", lambda: "wayland")
    engine, _ = make_engine()
    engine.start_hotkey()
    status = engine.status()["hotkey"]
    assert status.startswith("registered: Meta+L") and "System Settings" in status
    engine.settings["quick"]["shortcut"] = "Meta+L"
    assert engine.status()["hotkey"] == "registered: Meta+L"


def test_silent_recording_does_not_block_next_press(make_engine):
    from dictation4me.errors import AppError as Err

    def silent(path, cfg):
        raise Err("NO_SPEECH", "No speech was detected in the recording")

    engine, _ = make_engine(stt=silent)
    assert press(engine)["state"] == "RECORDING"
    assert press(engine)["state"] == "TRANSCRIBING"
    ready(engine, "IDLE")
    assert "No speech" in engine.status()["error"]
    recovery = engine.store.recordings(recoverable=True)
    assert recovery and recovery[0]["error_code"] == "NO_SPEECH"  # audio retained
    assert press(engine)["state"] == "RECORDING"  # next press just records again


def test_provider_failure_still_requires_explicit_recovery(make_engine):
    from dictation4me.errors import AppError as Err

    def down(path, cfg):
        raise Err("PROVIDER_HTTP", "Provider returned HTTP 500")

    engine, _ = make_engine(stt=down)
    press(engine)
    press(engine)
    ready(engine, "ERROR")
    assert press(engine)["ignored"] == "recovery_pending"


def test_credential_status_and_explicit_keyring_import(make_engine, monkeypatch):
    import dictation4me.credentials as creds
    store = {}
    monkeypatch.setattr(creds, "store_secret", lambda name, value: store.__setitem__(name, value))
    engine, _ = make_engine()
    engine.save_settings({"ai": {"key_source": "environment", "key_reference": "D4M_TEST_KEY"}})
    monkeypatch.delenv("D4M_TEST_KEY", raising=False)
    status = engine.credential_status()["ai"]
    assert not status["available"] and not status["can_import"]
    monkeypatch.setenv("D4M_TEST_KEY", "value-123")
    status = engine.handle("credentials.status", {})
    assert status["ai"]["available"] and status["ai"]["can_import"]
    assert "value-123" not in str(status)
    result = engine.handle("credentials.import-env", {"provider": "ai"})
    assert store == {"ai.default": "value-123"}
    assert result["settings"]["ai"]["key_source"] == "keyring"
    assert "value-123" not in str(result)


def test_two_press_flow_pastes_automatically(make_engine, monkeypatch):
    import dictation4me.desktop
    notes = []
    monkeypatch.setattr(dictation4me.desktop, "notify", notes.append)
    engine, backend = make_engine()
    engine.save_settings({"quick": {"auto_deliver": True, "output_mode": "attempt-paste"}})
    assert press(engine)["state"] == "RECORDING"
    assert press(engine)["state"] == "TRANSCRIBING"
    ready(engine, "RECOVERABLE_RESULT")
    assert backend.content == "Spoken text" and backend.paste_calls == 1
    assert "Dictation pasted into the active app" in notes
    assert not any("press shortcut to deliver" in n for n in notes)  # no 3rd-press prompt
    assert press(engine)["state"] == "RECORDING"  # third press starts the next dictation


def test_gui_flow_mapping():
    from dictation4me.gui import NotebookUI
    for flow in ("paste", "copy", "review"):
        quick = NotebookUI._flow_settings(flow)
        assert NotebookUI._flow(quick) == flow
