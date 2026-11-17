"""Resident application coordinator: serialized capture, durable jobs and note commands."""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__, audio, config, credentials, desktop, providers
from .errors import AppError, Busy, Conflict
from .paths import Paths
from .presets import Presets
from .storage import Notebook, digest


class Engine:
    """The resident service owns the only writable notebook database connection."""

    def __init__(self, paths: Paths, *, recorder_type=audio.AudioRecorder, stt=providers.transcribe,
                 ai=providers.transform, clipboard_factory=desktop.clipboard):
        self.paths = paths.init()
        self.settings = config.load(paths)
        self.store = Notebook(paths)
        self.presets = Presets(paths)
        self.store.recover_startup()
        # Cleanup precedes restoration of the persisted quick-output state, so
        # expired captures can never be reported as still ready to deliver.
        expired_paths = self.store.expire_quick()
        for rel in expired_paths:
            try:
                self.paths.media_path(rel).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        self.recorder_type = recorder_type
        self.stt = stt
        self.ai = ai
        self.clipboard_factory = clipboard_factory
        self._clipboard = None
        self._state_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=self.settings["service"]["max_provider_jobs"],
                                            thread_name_prefix="dictation-provider")
        self._generation = 0
        self._closing = False
        self._state = "IDLE"
        self._session: dict | None = None
        self._recording = None
        self._last_hotkey = 0.0
        self._cancelled_jobs: set[str] = set()
        self._last_error = ""
        self._hotkey = None
        self._hotkey_enabled = False
        self._hotkey_state = "not registered"
        last = self.store.quick()
        if last:
            if last["status"] == "ready":
                self._state = "READY"
                self._session = {"id": last["recording_id"], "quick_id": last["id"],
                                 "mode": "quick", "generation": self._generation,
                                 "target": None, "recording_id": last["recording_id"]}
            elif last["status"] == "delivered":
                self._state = "RECOVERABLE_RESULT"
            if last["status"] == "ready":
                # Crash during SendInput left an outcome-unknown delivery attempt.
                # Never silently replay with a new attempt ID on the next hotkey.
                attempts = self.store.recent_deliveries(last["id"])
                if any(a["paste_status"] == "unknown" for a in attempts):
                    self._state = "RECOVERABLE_RESULT"
            elif last["status"] == "error":
                self._state = "ERROR"

    @property
    def clipboard(self):
        if self._clipboard is None:
            self._clipboard = self.clipboard_factory()
        return self._clipboard

    def start_hotkey(self):
        self._hotkey_enabled = True
        with self._state_lock:
            old, self._hotkey = self._hotkey, None
        if old:
            try:
                old.close()
            except Exception:
                pass
        try:
            hotkey = desktop.shortcut(self.settings["quick"]["shortcut"], self.hotkey)
            hotkey.start()
            self._hotkey = hotkey
            self._hotkey_state = "waiting for desktop approval (check for a KDE shortcut dialog)"
        except Exception:
            self._hotkey_state = "unavailable; use a desktop command shortcut: dictation4me toggle"

    def close(self):
        with self._state_lock:
            self._closing = True
            if self._hotkey:
                self._hotkey.close()
            if self._recording:
                try:
                    info = self._recording.stop()
                    self.store.capture_finalized(self._session["recording_id"], info,
                                                 status="incomplete")
                except Exception:
                    pass
                self._recording = None
            self._generation += 1
        # Cancelling queued jobs and invalidating the generation makes in-flight
        # HTTP operations discard results when they eventually return. Never
        # attempt SQLite writes from a worker after the connection is closed.
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._clipboard and hasattr(self._clipboard, "close"):
            self._clipboard.close()
        self.store.close()

    def _touch_state(self, value: str, *, message: str = ""):
        self._state = value
        self._last_error = message
        if value in ("RECORDING", "READY", "ERROR", "RECOVERABLE_RESULT"):
            labels = {"RECORDING": "Recording started — press shortcut to stop",
                      "READY": "Dictation ready — press shortcut to deliver",
                      "ERROR": "Dictation needs attention — open Recovery",
                      "RECOVERABLE_RESULT": "Dictation copied; new shortcut starts another recording"}
            desktop.notify(labels[value])

    def housekeep(self) -> int:
        """Expire completed temporary dictation; unresolved audio is never culled."""
        if self._closing:
            return 0
        stale_audio = self.store.expire_quick()
        with self._state_lock:
            if (self._session and self._session.get("quick_id") and
                    self._state in ("READY", "RECOVERABLE_RESULT") and
                    not self.store.quick(self._session["quick_id"])):
                self._session = None
                self._touch_state("IDLE")
        for rel in stale_audio:
            try:
                self.paths.media_path(rel).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        return len(stale_audio)

    def status(self) -> dict:
        with self._state_lock:
            key_status = self._hotkey_state
            if self._hotkey and getattr(self._hotkey, "error", None):
                key_status = ("unavailable: " + str(self._hotkey.error)[:200] +
                              " (fallback: bind 'dictation4me toggle' in KDE)")
            elif self._hotkey and getattr(self._hotkey, "description", None):
                actual = str(self._hotkey.description)
                key_status = "registered: " + actual
                try:
                    differs = (config.normalize_shortcut(actual) !=
                               self.settings["quick"]["shortcut"])
                except AppError:
                    differs = False  # pending or a non-parsable desktop label
                if differs and desktop.session_type() == "wayland":
                    # KDE keeps the user's stored binding; the app's value is only
                    # the preferred trigger offered on first registration.
                    key_status += (" — KDE keeps this binding; change it in System Settings › "
                                   "Keyboard › Shortcuts › Dictation4me")
            return {"version": __version__, "state": self._state,
                    "error": self._last_error,
                    "recording_id": self._session.get("recording_id") if self._session else None,
                    "quick_id": self._session.get("quick_id") if self._session else None,
                    "mode": self._session.get("mode") if self._session else None,
                    "hotkey": key_status,
                    "session": desktop.session_type(),
                    "recovery_count": len(self.store.recordings(recoverable=True))}

    def _new_session(self, mode: str, note_id: str | None) -> dict:
        base_seq = self.store.get_note(note_id)["edit_seq"] if note_id else None
        target = None
        if mode == "quick":
            try:
                target = self.clipboard.foreground_token()
            except (AppError, OSError):
                pass
        created = self.store.capture_started(mode, note_id)
        self._generation += 1
        return {"generation": self._generation, "recording_id": created["recording_id"],
                "quick_id": created["quick_id"], "note_id": note_id, "base_edit_seq": base_seq,
                "mode": mode, "target": target}

    def start_capture(self, mode: str = "quick", note_id: str | None = None) -> dict:
        with self._state_lock:
            if self._state in ("RECORDING", "FINALIZING", "TRANSCRIBING", "TRANSFORMING", "DELIVERING"):
                raise Busy()
            if mode == "quick" and self._state == "ERROR":
                raise AppError("RECOVERY_PENDING", "Resolve/cancel previous quick capture before starting another")
            session = self._new_session(mode, note_id)
            self._session = session
            try:
                self._recording = self.recorder_type(self.paths, session["recording_id"], self.settings["audio"])
                self._recording.start()
            except Exception:
                self._recording = None
                self.store.set_recording_error(session["recording_id"], "AUDIO_OPEN_FAILED")
                self._touch_state("ERROR", message="Microphone unavailable; check audio permissions")
                raise
            self._touch_state("RECORDING")
            return {"state": self._state, **session}

    def stop_capture(self) -> dict:
        with self._state_lock:
            if self._state != "RECORDING" or not self._recording:
                raise AppError("INVALID_STATE", "No recording is active")
            session = dict(self._session)
            self._touch_state("FINALIZING")
            recording = self._recording
            self._recording = None
            try:
                info = recording.stop()
            except Exception as exc:
                self.store.set_recording_error(session["recording_id"], "AUDIO_FINALIZE_FAILED")
                self._touch_state("ERROR", message="Recording could not be finalized; recovery data was retained")
                raise AppError("AUDIO_FINALIZE_FAILED", "Audio finalization failed; check Recovery") from exc
            state = "ready" if info.get("file_relpath") and not info.get("error_code") else "incomplete"
            self.store.capture_finalized(session["recording_id"], info, status=state)
            if state != "ready":
                self._touch_state("ERROR", message=info.get("error_code") or "Incomplete audio")
                return {"state": self._state, "recording_id": session["recording_id"]}
            return self._submit_transcription(session)

    def _submit_transcription(self, session: dict, retry_of: str | None = None) -> dict:
        job = self.store.create_job("stt", session["recording_id"], self.settings["stt"]["model"],
                                    retry_of=retry_of)
        self._touch_state("TRANSCRIBING")
        self._executor.submit(self._transcribe_job, job["id"], dict(session),
                              dict(self.settings["stt"]))
        return {"state": self._state, "job_id": job["id"], "recording_id": session["recording_id"]}

    def _is_live(self, session: dict, job_id: str) -> bool:
        with self._state_lock:
            return (not self._closing and session["generation"] == self._generation and
                    job_id not in self._cancelled_jobs)

    def _transcribe_job(self, job_id: str, session: dict, stt_cfg: dict):
        try:
            self.store.job_status(job_id, "running")
            r = self.store.recording(session["recording_id"])
            if not r["file_relpath"]:
                raise AppError("AUDIO_MISSING", "Recording was not finalized")
            config.require_provider_consent(self.settings, "stt")
            result = self.stt(self.paths.media_path(r["file_relpath"]), stt_cfg)
            if not self._is_live(session, job_id):
                if not self._closing:
                    self.store.job_status(job_id, "cancelled")
                return
            self.store.transcript_saved(r["id"], result["text"], stt_cfg["model"],
                                        result.get("language"),
                                        self.settings["quick"]["quick_text_retention_hours"])
            # Note-mode appending uses the capture-time edit seq and CAS. Concurrent
            # typing takes precedence; raw transcript remains accessible for Append.
            if session["mode"] == "note" and session["note_id"]:
                try:
                    applied = self.store.append_transcript(r["id"], expected_edit_seq=session["base_edit_seq"])
                    if applied.get("applied"):
                        self.store.checkpoint(session["note_id"], kind="transcript")
                except (Conflict, AppError):
                    pass  # Transcript is safe; UI exposes explicit Append Text action.
            self.store.job_status(job_id, "succeeded")
            with self._state_lock:
                if not self._is_live(session, job_id):
                    return
                if session["mode"] == "note":
                    self._touch_state("IDLE")
                else:
                    self._touch_state("TRANSFORMING" if self.settings["ai"]["automatic_quick_preset_id"] else "READY")
            if session["mode"] == "quick" and self.settings["ai"]["automatic_quick_preset_id"]:
                self._quick_transform_auto(session)
            elif session["mode"] == "quick":
                self._after_result(session)
            self._cleanup_quick_audio(session)
        except AppError as exc:
            with self._state_lock:
                if self._closing:
                    return
                if self._is_live(session, job_id):
                    self.store.job_status(job_id, "failed", exc.code)
                    self.store.set_recording_error(session["recording_id"], exc.code)
                    self._touch_state("ERROR", message=str(exc))
                else:
                    self.store.job_status(job_id, "cancelled")
        except Exception:
            with self._state_lock:
                if self._closing:
                    return
                if self._is_live(session, job_id):
                    self.store.job_status(job_id, "failed", "INTERNAL")
                    self.store.set_recording_error(session["recording_id"], "INTERNAL")
                    self._touch_state("ERROR", message="Transcription failed; audio is retained in Recovery")

    def _quick_transform_auto(self, session: dict) -> None:
        if self._closing:
            return
        preset_id = self.settings["ai"]["automatic_quick_preset_id"]
        quick = self.store.quick(session["quick_id"])
        try:
            preset = self.presets.get(preset_id)
            job = self.store.create_job("ai", quick["id"], self.settings["ai"]["model"])
            self.store.job_status(job["id"], "running")
            config.require_provider_consent(self.settings, "ai")
            transformed = self.ai(quick["original_text"], self.settings["ai"], preset)
            if self._closing:
                return
            if session["generation"] == self._generation:
                # Never replace output the user manually edited while AI ran.
                latest = self.store.quick(quick["id"])
                if latest and latest["edited_text"] == quick["edited_text"]:
                    self.store.edit_quick(quick["id"], transformed)
                self.store.job_status(job["id"], "succeeded")
            else:
                self.store.job_status(job["id"], "cancelled")
        except AppError as exc:
            if self._closing:
                return
            if "job" in locals():
                self.store.job_status(job["id"], "failed", exc.code)
            # Preserve the original STT text and let the user deliver it explicitly.
            self._last_error = "Automatic AI rewrite failed; original transcript is available"
        self._after_result(session)

    def _after_result(self, session: dict):
        with self._state_lock:
            if session["generation"] != self._generation:
                return
            self._touch_state("READY")
            auto = session["mode"] == "quick" and self.settings["quick"]["auto_deliver"]
        if auto:
            try:
                self.deliver_quick(session["quick_id"])
            except AppError:
                # Keep READY output, do not automatically retry uncertain delivery.
                pass

    def _cleanup_quick_audio(self, session):
        if self._closing:
            return
        if (session["mode"] != "quick" or
            self.settings["quick"]["record_audio_success_retention"] != "delete_after_transcription"):
            return
        # Successful STT committed first. Leave file intact if saved as note.
        r = self.store.recording(session["recording_id"])
        q = self.store.quick(session["quick_id"])
        if not r["file_relpath"] or (q and q["saved_note_id"]):
            return
        with self.store.transaction():
            self.store.db.execute("UPDATE recordings SET status='deleted',file_relpath=NULL WHERE id=?",
                                  (r["id"],))
        try:
            self.paths.media_path(r["file_relpath"]).unlink(missing_ok=True)
        except OSError:
            pass

    def retry_recording(self, recording_id: str) -> dict:
        with self._state_lock:
            if self._state in ("RECORDING", "FINALIZING", "TRANSCRIBING", "TRANSFORMING", "DELIVERING"):
                raise Busy()
            r = self.store.recording(recording_id)
            if not r["file_relpath"] or not self.paths.media_path(r["file_relpath"]).is_file():
                raise AppError("AUDIO_MISSING", "There is no finalized recording to retry")
            with self.store.lock:
                if self.store._one("SELECT id FROM transcripts WHERE recording_id=?", (r["id"],)):
                    raise AppError("ALREADY_PROCESSED", "This recording already has a transcript")
                previous = self.store._one("SELECT id FROM jobs WHERE source_id=? AND operation='stt' "
                                           "ORDER BY created_at DESC LIMIT 1", (r["id"],))
            self._generation += 1
            note_seq = self.store.get_note(r["note_id"])["edit_seq"] if r["note_id"] else None
            session = {"generation": self._generation, "recording_id": r["id"], "quick_id": r["id"]
                       if r["mode"] == "quick" else None, "mode": r["mode"], "note_id": r["note_id"],
                       "base_edit_seq": note_seq, "target": None}
            self._session = session
            with self.store.transaction():
                self.store.db.execute("UPDATE recordings SET status='ready',error_code=NULL WHERE id=?", (r["id"],))
                if r["mode"] == "quick":
                    self.store.db.execute("UPDATE quick_items SET status='processing' WHERE recording_id=?", (r["id"],))
            return self._submit_transcription(session, retry_of=previous["id"] if previous else None)

    def _clipboard_copy(self, text: str):
        self.clipboard.copy(text)
        if self.clipboard.read() != text:
            raise AppError("CLIPBOARD_CHANGED", "Clipboard content changed; paste was not sent")

    def deliver_quick(self, quick_id: str | None = None, attempt_id: str | None = None,
                      *, allow_repeat: bool = False) -> dict:
        """One delivery attempt per persisted idempotency key.

        Persist the intent *before* touching the clipboard or injecting keys. A
        crash after an unknown paste outcome must not silently replay text.
        """
        with self._state_lock:
            if self._state in ("RECORDING", "FINALIZING", "TRANSCRIBING", "TRANSFORMING", "DELIVERING"):
                raise Busy("Delivery unavailable until processing finishes")
            item = self.store.quick(quick_id)
            if not item or not item["edited_text"] or item["status"] not in ("ready", "delivered"):
                raise AppError("NO_RESULT", "No completed quick dictation is available")
            frozen = item["edited_text"]
            job_id = attempt_id or str(uuid.uuid4())
            fingerprint = digest(frozen)
            old = next((d for d in self.store.recent_deliveries(item["id"]) if d["id"] == job_id), None)
            if old:
                if old["content_sha256"] != fingerprint:
                    raise Conflict("Delivery request ID was reused with different content")
                return old  # persisted status; never re-send an uncertain attempt
            if item["status"] == "delivered" and not allow_repeat:
                raise AppError("DELIVERED", "Already delivered. Use explicit Paste last to retry (duplicates possible)")
            expected = (self._session.get("target") if self._session and
                        self._session.get("quick_id") == item["id"] else None)
            should_paste = self.settings["quick"]["output_mode"] == "attempt-paste"
            # Not-attempted means a crash could have occurred at any point before
            # recording a confirmed clipboard result. It is not auto-retried.
            self.store.delivery(item["id"], job_id, fingerprint, "not_attempted",
                                "unknown" if should_paste else "not_attempted",
                                target_hint="present" if expected else None)
            self._touch_state("DELIVERING")
        try:
            self._clipboard_copy(frozen)
            self.store.update_delivery(job_id, clipboard="copied")
            status = "not_attempted"
            if should_paste:
                try:
                    if desktop.session_type() == "windows":
                        status = self.clipboard.paste(self.settings["quick"]["paste_shortcut"],
                                                      expected_target=expected)
                    else:
                        status = self.clipboard.paste(
                            self.settings["quick"]["paste_shortcut"],
                            unsafe_wayland=self.settings["quick"]["paste_to_current_focus_unsafe_wayland"])
                except Exception:
                    status = "blocked"
                self.store.update_delivery(job_id, paste=status)
            self.store.mark_quick(item["id"], "delivered")
            with self._state_lock:
                self._touch_state("RECOVERABLE_RESULT")
            return {"attempt_id": job_id, "clipboard_status": "copied", "paste_status": status,
                    "meaning": "Shortcut sent, if reported; actual field insertion cannot be verified"}
        except AppError as exc:
            self.store.update_delivery(job_id, clipboard="failed", paste="not_attempted")
            with self._state_lock:
                self._touch_state("READY", message=str(exc))
            raise

    def copy_last(self) -> dict:
        item = self.store.quick()
        if not item or not item.get("edited_text"):
            raise AppError("NO_RESULT", "No completed quick dictation to copy")
        self._clipboard_copy(item["edited_text"])
        return {"clipboard_status": "copied", "characters": len(item["edited_text"])}

    def hotkey(self) -> dict:
        with self._state_lock:
            current_time = time.monotonic()
            if current_time - self._last_hotkey < .35:
                return {"ignored": "debounced", "state": self._state}
            self._last_hotkey = current_time
            state = self._state
        if state in ("IDLE", "CANCELLED", "RECOVERABLE_RESULT"):
            return self.start_capture("quick")
        if state == "RECORDING":
            return self.stop_capture()
        if state == "READY":
            return self.deliver_quick()
        if state in ("FINALIZING", "TRANSCRIBING", "TRANSFORMING", "DELIVERING"):
            desktop.notify("Still processing the last dictation")
            return {"ignored": "busy", "state": state}
        # Spec: ERROR with a durable recording takes no implicit action, but the
        # press must not be silent or it looks like a broken shortcut.
        desktop.notify("Last dictation failed (" + (self._last_error or "error")[:80] +
                       "). Open Recovery, or Cancel to start a new one")
        return {"ignored": "recovery_pending", "state": state}

    def cancel(self) -> dict:
        with self._state_lock:
            if self._state == "RECORDING" and self._recording:
                info = self._recording.stop()
                self._recording = None
                self.store.capture_finalized(self._session["recording_id"], info, status="incomplete")
                if self._session["quick_id"]:
                    self.store.mark_quick(self._session["quick_id"], "cancelled")
            elif self._state in ("TRANSCRIBING", "TRANSFORMING"):
                if self._session:
                    latest = self.store._one("SELECT id FROM jobs WHERE source_id=? ORDER BY created_at DESC LIMIT 1",
                                             (self._session["recording_id"],))
                    if latest:
                        self._cancelled_jobs.add(latest["id"])
                if self._session and self._session["quick_id"]:
                    self.store.mark_quick(self._session["quick_id"], "cancelled")
            self._generation += 1
            self._session = None
            self._touch_state("CANCELLED")
            return {"state": self._state, "message": "Operation cancelled; durable recording retained"}

    def transform_note(self, note_id: str, preset_id: str) -> dict:
        config.require_provider_consent(self.settings, "ai")
        note = self.store.get_note(note_id)
        preset = self.presets.get(preset_id)
        cfg = dict(self.settings["ai"])
        source = note["content_md"]
        job = self.store.create_job("ai", note_id, cfg["model"])
        self._executor.submit(self._transform_job, job["id"], note, preset, source, cfg)
        return {"job_id": job["id"], "status": "queued"}

    def _transform_job(self, job_id: str, note: dict, preset: dict, source: str, ai_cfg: dict):
        try:
            if self._closing:
                return
            self.store.job_status(job_id, "running")
            output = self.ai(source, ai_cfg, preset)
            if self._closing:
                return
            if job_id in self._cancelled_jobs:
                self.store.job_status(job_id, "cancelled")
                return
            self.store.proposal_saved(job_id, note["id"], note["current_revision_id"],
                                      note["edit_seq"], digest(source), preset["id"],
                                      preset["instruction"], ai_cfg["model"], output)
            self.store.job_status(job_id, "succeeded")
        except AppError as exc:
            if not self._closing:
                self.store.job_status(job_id, "failed", exc.code)
        except Exception:
            if not self._closing:
                self.store.job_status(job_id, "failed", "INTERNAL")

    def transform_quick(self, quick_id: str, preset_id: str) -> dict:
        config.require_provider_consent(self.settings, "ai")
        item = self.store.quick(quick_id)
        if not item or not item["edited_text"]:
            raise AppError("NO_RESULT", "No quick text available")
        cfg = dict(self.settings["ai"])
        preset = self.presets.get(preset_id)
        job = self.store.create_job("ai", quick_id, cfg["model"])
        source = item["edited_text"]
        self._executor.submit(self._quick_proposal_job, job["id"], item["id"], source, cfg, preset)
        return {"job_id": job["id"], "status": "queued"}

    def _quick_proposal_job(self, job_id: str, quick_id: str, source: str, cfg: dict, preset: dict):
        try:
            if self._closing:
                return
            self.store.job_status(job_id, "running")
            output = self.ai(source, cfg, preset)
            if self._closing:
                return
            self.store.proposal_saved(job_id, None, None, None, digest(source), preset["id"],
                                      preset["instruction"], cfg["model"], output)
            self.store.job_status(job_id, "succeeded")
        except AppError as exc:
            if not self._closing:
                self.store.job_status(job_id, "failed", exc.code)
        except Exception:
            if not self._closing:
                self.store.job_status(job_id, "failed", "INTERNAL")

    def accept_quick_proposal(self, quick_id: str, proposal_id: str) -> dict:
        proposal = self.store.proposal(proposal_id)
        q = self.store.quick(quick_id)
        owner_job = self.store.job(proposal["job_id"])
        if (owner_job["operation"] != "ai" or owner_job["source_id"] != quick_id or
            proposal["status"] != "pending" or proposal["source_note_id"] is not None
                or not q or digest(q["edited_text"] or "") != proposal["source_sha256"]):
            raise Conflict("Quick text changed after this AI request; review or create a new note")
        changed = self.store.edit_quick(quick_id, proposal["proposed_content_md"])
        self.store.accept_proposal_status(proposal_id)  # retain an accepted audit record
        return changed

    def get_job_result(self, job_id: str) -> dict:
        job = self.store.job(job_id)
        result = {"job": job}
        if job["status"] == "succeeded" and job["operation"] == "ai":
            with self.store.lock:
                result["proposal"] = self.store._one("SELECT * FROM ai_proposals WHERE job_id=?", (job_id,))
        return result

    def recover_salvage(self, recording_id: str) -> dict:
        r = self.store.recording(recording_id)
        part = self.paths.spool / (recording_id + ".part")
        if r["status"] not in ("incomplete", "error") or not part.is_file():
            raise AppError("AUDIO_MISSING", "No interrupted .part WAV is available")
        repaired = audio.repair_spool(part)
        final = self.paths.audio / (recording_id + ".wav")
        if final.exists():
            raise AppError("EXISTS", "Recovered WAV destination already exists")
        os.replace(part, final)
        raw = final.read_bytes()
        result = self.store.salvage_recording(recording_id, {
            "file_relpath": "audio/" + final.name, "size_bytes": len(raw),
            "duration_ms": int(repaired["frames"] / repaired["rate"] * 1000),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        })
        return {"recording_id": recording_id, "status": result["status"], "ready_to_retry": True}

    def recover_delete(self, recording_id: str) -> dict:
        r = self.store.recording(recording_id)
        if r["status"] not in ("error", "incomplete", "ready"):
            raise AppError("INVALID", "Only unresolved recordings can be discarded")
        # Delete via DB first; lingering unlinked file after crash is safer than
        # missing the only copy of an unresolved recording.
        with self.store.transaction():
            self.store.db.execute("UPDATE recordings SET status='deleted',file_relpath=NULL,"
                                  "spool_name=NULL WHERE id=?", (recording_id,))
            self.store.db.execute("UPDATE quick_items SET status='cancelled' WHERE recording_id=?", (recording_id,))
        if r["file_relpath"]:
            try:
                self.paths.media_path(r["file_relpath"]).unlink(missing_ok=True)
            except OSError:
                pass
        if r["spool_name"]:
            (self.paths.spool / (recording_id + ".part")).unlink(missing_ok=True)
        with self._state_lock:
            if self._state == "ERROR" and self._session and self._session["recording_id"] == recording_id:
                self._touch_state("CANCELLED")
        return {"deleted": recording_id}

    def save_settings(self, changes: dict, secrets: dict | None = None) -> dict:
        secrets = secrets or {}
        if secrets:
            for provider, secret in secrets.items():
                if provider not in ("stt", "ai") or not isinstance(secret, str):
                    raise AppError("INVALID", "Invalid credential update")
                if not secret:
                    continue
                reference = f"{provider}.default"
                credentials.store_secret(reference, secret)
                changes.setdefault(provider, {}).update({"key_source": "keyring", "key_reference": reference})
        old_shortcut = self.settings["quick"]["shortcut"]
        self.settings = config.merge(self.paths, changes)
        changed = old_shortcut != self.settings["quick"]["shortcut"]
        if changed and self._hotkey_enabled:
            # Rebind in the resident service; no manual restart.
            self.start_hotkey()
        return {"settings": self.settings, "restart_required": False,
                "shortcut_changed": changed, "hotkey": self.status()["hotkey"]}

    def handle(self, action: str, args: dict) -> dict | list | str | None:
        """Small fixed RPC vocabulary. No arbitrary Python/shell execution."""
        if action == "status":
            return self.status()
        if action == "config.get":
            return self.settings
        if action == "config.save":
            return self.save_settings(args.get("changes", {}), args.get("secrets"))
        if action == "notes.list":
            return self.store.list_notes(search=args.get("search", ""), scope=args.get("scope", "active"),
                                         folder_id=args.get("folder_id"), limit=args.get("limit", 50),
                                         offset=args.get("offset", 0))
        if action == "notes.get":
            return self.store.get_note(args["id"])
        if action == "notes.create":
            return self.store.create_note(args.get("title", "Untitled"), args.get("content", ""),
                                          args.get("folder_id"))
        if action == "notes.draft":
            return self.store.save_draft(args["id"], args["text"], args["base_edit_seq"])
        if action == "notes.fields":
            return self.store.set_note_fields(args["id"], **args.get("fields", {}))
        if action == "notes.checkpoint":
            return self.store.checkpoint(args["id"])
        if action == "notes.revisions":
            return self.store.revisions(args["id"])
        if action == "notes.revision":
            return self.store.revision(args["id"])
        if action == "notes.restore":
            return self.store.restore_revision(args["note_id"], args["revision_id"], args["base_edit_seq"])
        if action == "notes.purge":
            paths = self.store.hard_delete(args["id"])
            for rel in paths:
                try:
                    self.paths.media_path(rel).unlink(missing_ok=True)
                except OSError:
                    pass
            return {"deleted": args["id"]}
        if action == "folders.list":
            return self.store.list_folders()
        if action == "folders.create":
            return self.store.add_folder(args["name"])
        if action == "presets.list":
            return self.presets.list()
        if action == "presets.save":
            return self.presets.save(args["preset"])
        if action == "presets.delete":
            self.presets.delete(args["id"])
            return {"deleted": args["id"]}
        if action == "capture.start":
            return self.start_capture(args.get("mode", "quick"), args.get("note_id"))
        if action == "capture.stop":
            return self.stop_capture()
        if action == "capture.toggle":
            return self.hotkey()
        if action == "capture.cancel":
            return self.cancel()
        if action == "recordings.note":
            return self.store.recordings(note_id=args["note_id"])
        if action == "recordings.recovery":
            return self.store.recordings(recoverable=True)
        if action == "recordings.append":
            return self.store.append_transcript(args["id"])
        if action == "recordings.retry":
            return self.retry_recording(args["id"])
        if action == "recordings.salvage":
            return self.recover_salvage(args["id"])
        if action == "recordings.delete":
            return self.recover_delete(args["id"])
        if action == "recordings.play":
            r = self.store.recording(args["id"])
            if not r["file_relpath"]:
                raise AppError("AUDIO_MISSING", "Recording audio was deleted")
            path = self.paths.media_path(r["file_relpath"])
            self._executor.submit(audio.play, path)
            return {"status": "playing"}
        if action == "quick.get":
            return self.store.quick(args.get("id"))
        if action == "quick.edit":
            return self.store.edit_quick(args["id"], args["text"])
        if action == "quick.deliver":
            return self.deliver_quick(args.get("id"), args.get("attempt_id"),
                                      allow_repeat=args.get("allow_repeat", False))
        if action == "quick.copy":
            return self.copy_last()
        if action == "quick.save-note":
            return self.store.save_quick_as_note(args["id"])
        if action == "ai.note":
            return self.transform_note(args["note_id"], args["preset_id"])
        if action == "ai.quick":
            return self.transform_quick(args["quick_id"], args["preset_id"])
        if action == "ai.job":
            return self.get_job_result(args["id"])
        if action == "ai.proposal":
            return self.store.proposal(args["id"])
        if action == "ai.proposals":
            return self.store.proposals(args["note_id"])
        if action == "ai.accept":
            return self.store.apply_proposal(args["id"], args["action"])
        if action == "ai.accept-quick":
            return self.accept_quick_proposal(args["quick_id"], args["id"])
        if action == "ai.discard":
            self.store.discard_proposal(args["id"])
            return {"discarded": args["id"]}
        if action == "integrity":
            return self.store.integrity()
        if action == "audio.devices":
            return audio.devices()
        if action == "provider.test-ai":
            config.require_provider_consent(self.settings, "ai")
            preset = {"id": "test", "instruction": "Reply with the word OK only.",
                      "output_format": "plain", "preserve_language": False}
            answer = self.ai("test", dict(self.settings["ai"]), preset)
            return {"status": "success", "characters": len(answer)}
        if action == "provider.test-stt":
            config.require_provider_consent(self.settings, "stt")
            path = Path(args["file"]).resolve()
            if path.suffix.lower() != ".wav":
                raise AppError("INVALID", "Choose a .wav audio file")
            answer = self.stt(path, dict(self.settings["stt"]))
            return {"status": "success", "characters": len(answer["text"])}
        if action == "service.stop":
            return {"stopping": True}  # the RPC server processes shutdown after reply
        raise AppError("UNKNOWN_ACTION", "Unsupported resident-service action")
