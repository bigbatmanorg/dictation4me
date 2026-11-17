import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from dictation4me.errors import AppError, Conflict
from dictation4me.storage import Notebook, digest


def test_create_edit_checkpoint_search_and_immutability(app_paths):
    db = Notebook(app_paths)
    n = db.create_note("Czech and emoji 📝", "Ahoj světe")
    assert len(db.list_notes(search="světe")) == 1
    assert len(db.search_fts("Ahoj")) == 1
    assert db.save_draft(n["id"], "updated content", 0)["edit_seq"] == 1
    assert not db.list_notes(search="světe")
    assert len(db.list_notes(search="updated")) == 1
    with pytest.raises(Conflict):
        db.save_draft(n["id"], "stale write", 0)
    r = db.checkpoint(n["id"])
    assert r["created"] is True
    assert db.checkpoint(n["id"])["created"] is False
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction():
            db.db.execute("UPDATE note_revisions SET content_md='bad' WHERE id=?", (r["revision_id"],))
    assert db.integrity() == {"database": "ok", "foreign_keys": 0, "fts": True,
                                    "notes": 1, "recordings": 0}
    db.close()


def test_restore_is_new_revision(app_paths):
    db = Notebook(app_paths)
    note = db.create_note("T", "Original")
    db.save_draft(note["id"], "Edited", 0)
    db.checkpoint(note["id"])
    restored = db.restore_revision(note["id"], note["current_revision_id"], 1)
    assert restored["created"] is True
    assert db.get_note(note["id"])["content_md"] == "Original"
    assert len(db.revisions(note["id"])) == 3
    db.close()


def test_tags_folders_archive_trash_and_purge(app_paths):
    db = Notebook(app_paths)
    folder = db.add_folder("Ideas")
    n = db.create_note("some", "content", folder["id"])
    n = db.set_note_fields(n["id"], tags=["AI", "voice", "AI"], pinned=True)
    assert set(n["tags"]) == {"AI", "voice"}
    assert db.list_notes(folder_id=folder["id"])[0]["id"] == n["id"]
    db.set_note_fields(n["id"], archived=True)
    assert not db.list_notes()
    assert db.list_notes(scope="archive")
    db.set_note_fields(n["id"], archived=False, trashed=True)
    assert not db.list_notes()
    assert db.list_notes(scope="trash")
    with pytest.raises(AppError):
        db.save_draft(n["id"], "bad", 0)
    db.save_draft if False else None
    db.hard_delete(n["id"])
    assert db.integrity()["foreign_keys"] == 0
    assert db.integrity()["notes"] == 0
    db.close()


def test_draft_concurrency_cas(app_paths):
    db = Notebook(app_paths)
    note = db.create_note("hi", "")
    def writer(suffix):
        try:
            return db.save_draft(note["id"], suffix, 0)
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(writer, [str(i) for i in range(8)]))
    assert sum(x is not None for x in results) == 1
    assert db.get_note(note["id"])["edit_seq"] == 1
    db.close()


def test_transcription_appending_once_and_conflict(app_paths):
    db = Notebook(app_paths)
    n = db.create_note("Voice", "Already here")
    r = db.capture_started("note", n["id"])["recording_id"]
    db.capture_finalized(r, {"file_relpath": "audio/abc.wav", "size_bytes": 100}, status="ready")
    db.transcript_saved(r, "spoken text", "stt-model")
    db.save_draft(n["id"], "Typed while waiting", 0)
    with pytest.raises(Conflict):
        db.append_transcript(r, expected_edit_seq=0)
    assert db.append_transcript(r)["applied"] is True
    assert db.append_transcript(r)["already_applied"] is True
    assert db.get_note(n["id"])["content_md"] == "Typed while waiting\n\nspoken text"
    db.close()


def test_ai_proposals_non_destructive_with_edit_conflict(app_paths):
    db = Notebook(app_paths)
    n = db.create_note("My note", "Original content")
    job = db.create_job("ai", n["id"], "model")
    p = db.proposal_saved(job["id"], n["id"], n["current_revision_id"], 0, digest("Original content"),
                          "make-concise", "Shorten", "model", "New content")
    assert db.get_note(n["id"])["content_md"] == "Original content"
    db.save_draft(n["id"], "My latest manual edit", 0)
    with pytest.raises(Conflict):
        db.apply_proposal(p["id"], "current")
    alt = db.apply_proposal(p["id"], "alternative")
    assert alt["selected"] is False
    assert db.get_note(n["id"])["content_md"] == "My latest manual edit"
    assert db.revision(alt["revision_id"])["content_md"] == "New content"
    db.close()


def test_ai_accept_current_updates_current_draft(app_paths):
    db = Notebook(app_paths)
    n = db.create_note("Title", "start")
    job = db.create_job("ai", n["id"], "m")
    p = db.proposal_saved(job["id"], n["id"], n["current_revision_id"], 0,
                          digest("start"), "clean", "Fix", "m", "finished")
    result = db.apply_proposal(p["id"], "current")
    assert result["selected"]
    assert db.get_note(n["id"])["content_md"] == "finished"
    assert db.get_note(n["id"])["edit_seq"] == 1
    db.close()


def test_restart_interrupted_jobs_and_recordings(app_paths):
    db = Notebook(app_paths)
    c = db.capture_started("quick")
    job = db.create_job("stt", c["recording_id"], "fake")
    db.job_status(job["id"], "running")
    db.close()
    new = Notebook(app_paths)
    status = new.recover_startup()
    assert new.job(job["id"])["status"] == "interrupted"
    assert new.recording(c["recording_id"])["status"] == "incomplete"
    assert new.quick(c["quick_id"])["status"] == "error"
    assert status["recovery_pending"] == 1
    new.close()


def test_foreign_revision_assignment_rejected(app_paths):
    db = Notebook(app_paths)
    a = db.create_note("a")
    b = db.create_note("b")
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction():
            db.db.execute("UPDATE notes SET current_revision_id=? WHERE id=?", (b["current_revision_id"], a["id"]))
    db.close()
