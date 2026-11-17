"""Transactional single-writer SQLite notebook storage and durable recovery metadata."""
from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

from .errors import AppError, Conflict
from .paths import Paths


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def identifier() -> str:
    return str(uuid.uuid4())


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_dict(row) -> dict | None:
    return dict(row) if row is not None else None


class Notebook:
    """A serialized SQLite connection; all writes are transactional and owned by the service."""

    def __init__(self, paths: Paths):
        self.paths = paths.init()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(paths.database, check_same_thread=False, isolation_level=None,
                                  timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.execute("PRAGMA journal_mode = WAL")
        if not self.db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone():
            text = files("dictation4me").joinpath("assets/schema.sql").read_text("utf-8")
            self.db.executescript(text)
        version = self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if version == 1:
            # Extension of the baseline contract: idempotency for transcript insertion.
            # Existing v1 notebooks are upgraded transactionally without data loss.
            self.db.executescript("""BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS note_recording_applies (
                  recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
                  note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                  applied_at TEXT NOT NULL
                );
                INSERT INTO schema_migrations(version, applied_at, description)
                VALUES(2, datetime('now'), 'Idempotent recording-to-note insertions');
                COMMIT;""")
            version = 2
        if version != 2:
            raise AppError("SCHEMA", f"Database schema {version} is not supported (expected 2)")
        self.search_enabled = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='note_search_fts'"
        ).fetchone() is not None

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.close()

    def _one(self, sql: str, params=()) -> dict | None:
        return row_dict(self.db.execute(sql, params).fetchone())

    def _all(self, sql: str, params=()) -> list[dict]:
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def _note(self, note_id: str) -> dict:
        note = self._one("SELECT * FROM notes WHERE id=?", (note_id,))
        if not note:
            raise AppError("NOT_FOUND", "Note not found")
        return note

    def _content(self, note_id: str) -> tuple[str, int, str | None]:
        d = self._one("SELECT * FROM note_drafts WHERE note_id=?", (note_id,))
        if d:
            return d["content_md"], d["edit_seq"], d["base_revision_id"]
        n = self._note(note_id)
        if n["current_revision_id"]:
            r = self._one("SELECT content_md FROM note_revisions WHERE id=?", (n["current_revision_id"],))
            return r["content_md"], 0, n["current_revision_id"]
        return "", 0, None

    def _index(self, note_id: str, title: str, content: str) -> None:
        self.db.execute("INSERT INTO note_search_docs(note_id,title,body) VALUES(?,?,?) "
                        "ON CONFLICT(note_id) DO UPDATE SET title=excluded.title,body=excluded.body",
                        (note_id, title, content))

    def create_note(self, title: str = "Untitled", content: str = "", folder_id: str | None = None,
                    kind: str = "initial") -> dict:
        title = (title or "Untitled").strip()[:500]
        if len(content) > 500000:
            raise AppError("TOO_LARGE", "Note exceeds 500,000 characters")
        if kind not in ("initial", "import", "transcript"):
            raise AppError("INVALID", "Invalid note origin")
        ident, revision, t = identifier(), identifier(), now()
        with self.transaction():
            self.db.execute("INSERT INTO notes(id,title,folder_id,current_revision_id,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?)", (ident, title, folder_id, None, t, t))
            self.db.execute("INSERT INTO note_revisions(id,note_id,kind,content_md,created_at) "
                            "VALUES(?,?,?,?,?)", (revision, ident, kind, content, t))
            self.db.execute("UPDATE notes SET current_revision_id=? WHERE id=?", (revision, ident))
            self.db.execute("INSERT INTO note_drafts(note_id,base_revision_id,content_md,edit_seq,updated_at) "
                            "VALUES(?,?,?,0,?)", (ident, revision, content, t))
            self._index(ident, title, content)
        return self.get_note(ident)

    def get_note(self, note_id: str) -> dict:
        with self.lock:
            n = self._note(note_id)
            body, seq, base = self._content(note_id)
            n.update({"content_md": body, "edit_seq": seq, "base_revision_id": base,
                      "tags": [r["name"] for r in self._all(
                          "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id=t.id "
                          "WHERE nt.note_id=? ORDER BY t.name COLLATE NOCASE", (note_id,))]})
            return n

    def list_notes(self, *, search: str = "", scope: str = "active", folder_id: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
        limit = max(1, min(100, int(limit)))
        offset = max(0, min(1000000, int(offset)))
        if scope not in ("active", "archive", "trash", "all"):
            raise AppError("INVALID", "Unknown note scope")
        wheres = []
        params = []
        if scope == "active":
            wheres += ["n.trashed_at IS NULL", "n.archived_at IS NULL"]
        elif scope == "archive":
            wheres += ["n.trashed_at IS NULL", "n.archived_at IS NOT NULL"]
        elif scope == "trash":
            wheres.append("n.trashed_at IS NOT NULL")
        if folder_id:
            wheres.append("n.folder_id=?")
            params.append(folder_id)
        q = search.strip()[:200]
        if q:
            # Avoid parsing untrusted FTS syntax. Substring match is predictable and indexed
            # content remains transactionally projected for future FTS enhancements.
            wheres.append("(instr(lower(s.title),lower(?))>0 OR instr(lower(s.body),lower(?))>0)")
            params += [q, q]
        clause = " WHERE " + " AND ".join(wheres) if wheres else ""
        sql = ("SELECT n.id,n.title,n.folder_id,n.pinned,n.archived_at,n.trashed_at,n.created_at,"
               "n.updated_at,substr(s.body,1,180) AS excerpt FROM notes n "
               "LEFT JOIN note_search_docs s ON s.note_id=n.id" + clause +
               " ORDER BY n.pinned DESC,n.updated_at DESC LIMIT ? OFFSET ?")
        with self.lock:
            return self._all(sql, tuple(params + [limit, offset]))

    def search_fts(self, query: str, limit: int = 50) -> list[dict]:
        """Optional advanced FTS search. Plain substring list_notes never requires FTS syntax."""
        with self.lock:
            if not self.search_enabled:
                return self.list_notes(search=query, limit=limit)
            tokens = [x for x in query.split() if x]
            if not tokens:
                return []
            clean = " ".join('"' + t.replace('"', '""') + '"' for t in tokens[:16])
            return self._all("SELECT n.id,n.title,n.updated_at,substr(s.body,1,180) excerpt "
                             "FROM note_search_fts f JOIN note_search_docs s ON s.rowid=f.rowid "
                             "JOIN notes n ON n.id=s.note_id WHERE note_search_fts MATCH ? "
                             "AND n.trashed_at IS NULL LIMIT ?", (clean, min(100, limit)))

    def save_draft(self, note_id: str, text: str, base_edit_seq: int) -> dict:
        if len(text) > 500000:
            raise AppError("TOO_LARGE", "Note exceeds 500,000 characters")
        with self.transaction():
            n = self._note(note_id)
            if n["trashed_at"]:
                raise AppError("TRASHED", "Restore the note before editing")
            old, seq, base = self._content(note_id)
            if int(base_edit_seq) != seq:
                raise Conflict()
            if text != old:
                seq += 1
                self.db.execute("INSERT INTO note_drafts(note_id,base_revision_id,content_md,edit_seq,updated_at) "
                                "VALUES(?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET "
                                "content_md=excluded.content_md,edit_seq=excluded.edit_seq,"
                                "updated_at=excluded.updated_at", (note_id, base, text, seq, now()))
                self.db.execute("UPDATE notes SET updated_at=? WHERE id=?", (now(), note_id))
                self._index(note_id, n["title"], text)
            return {"edit_seq": seq, "updated_at": now(), "sha256": digest(text)}

    def set_note_fields(self, note_id: str, *, title=None, folder_id="__unchanged__", tags=None,
                        pinned=None, archived=None, trashed=None) -> dict:
        with self.transaction():
            self._note(note_id)  # raises NOT_FOUND for unknown notes
            parts, values = [], []
            if title is not None:
                title = title.strip()[:500] or "Untitled"
                parts.append("title=?")
                values.append(title)
            if folder_id != "__unchanged__":
                parts.append("folder_id=?")
                values.append(folder_id or None)
            if pinned is not None:
                parts.append("pinned=?")
                values.append(int(bool(pinned)))
            if archived is not None:
                parts.append("archived_at=?")
                values.append(now() if archived else None)
            if trashed is not None:
                parts.append("trashed_at=?")
                values.append(now() if trashed else None)
            if parts:
                self.db.execute("UPDATE notes SET " + ",".join(parts) + ",updated_at=? WHERE id=?",
                                tuple(values + [now(), note_id]))
            if tags is not None:
                if not isinstance(tags, list) or len(tags) > 30:
                    raise AppError("INVALID", "Tags must be a short list")
                self.db.execute("DELETE FROM note_tags WHERE note_id=?", (note_id,))
                for name in dict.fromkeys(x.strip()[:120] for x in tags if isinstance(x, str) and x.strip()):
                    self.db.execute("INSERT OR IGNORE INTO tags(id,name) VALUES(?,?)", (identifier(), name))
                    tag_id = self._one("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,))["id"]
                    self.db.execute("INSERT INTO note_tags(note_id,tag_id) VALUES(?,?)", (note_id, tag_id))
            if title is not None:
                content, _, _ = self._content(note_id)
                self._index(note_id, title, content)
        return self.get_note(note_id)

    def list_folders(self) -> list[dict]:
        with self.lock:
            return self._all("SELECT * FROM folders ORDER BY sort_order,name COLLATE NOCASE")

    def add_folder(self, name: str) -> dict:
        name = name.strip()[:200]
        if not name:
            raise AppError("INVALID", "Folder name cannot be empty")
        ident = identifier()
        with self.transaction():
            self.db.execute("INSERT INTO folders(id,name,created_at) VALUES(?,?,?)", (ident, name, now()))
        return {"id": ident, "name": name}

    def checkpoint(self, note_id: str, *, kind: str = "manual", force: bool = False) -> dict:
        if kind not in ("manual", "transcript", "restore", "ai_accepted", "import"):
            raise AppError("INVALID", "Unsupported revision kind")
        with self.transaction():
            n = self._note(note_id)
            body, seq, _ = self._content(note_id)
            parent_id = n["current_revision_id"]
            old = self._one("SELECT content_md FROM note_revisions WHERE id=?", (parent_id,)) if parent_id else None
            if not force and old and old["content_md"] == body:
                return {"revision_id": parent_id, "edit_seq": seq, "created": False}
            revision = identifier()
            self.db.execute("INSERT INTO note_revisions(id,note_id,parent_revision_id,kind,content_md,source_sha256,created_at) "
                            "VALUES(?,?,?,?,?,?,?)", (revision, note_id, parent_id, kind, body,
                                                     digest(body), now()))
            self.db.execute("UPDATE notes SET current_revision_id=?,updated_at=? WHERE id=?",
                            (revision, now(), note_id))
            self.db.execute("INSERT INTO note_drafts(note_id,base_revision_id,content_md,edit_seq,updated_at) "
                            "VALUES(?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET "
                            "base_revision_id=excluded.base_revision_id,updated_at=excluded.updated_at",
                            (note_id, revision, body, seq, now()))
            self._index(note_id, n["title"], body)
            return {"revision_id": revision, "edit_seq": seq, "created": True}

    def revisions(self, note_id: str, limit: int = 100) -> list[dict]:
        with self.lock:
            self._note(note_id)
            return self._all("SELECT id,parent_revision_id,kind,created_at,length(content_md) size "
                             "FROM note_revisions WHERE note_id=? ORDER BY created_at DESC LIMIT ?",
                             (note_id, min(200, limit)))

    def revision(self, revision_id: str) -> dict:
        with self.lock:
            result = self._one("SELECT * FROM note_revisions WHERE id=?", (revision_id,))
            if not result:
                raise AppError("NOT_FOUND", "Revision not found")
            return result

    def restore_revision(self, note_id: str, revision_id: str, base_edit_seq: int) -> dict:
        rev = self.revision(revision_id)
        if rev["note_id"] != note_id:
            raise AppError("INVALID", "Revision belongs to another note")
        with self.lock:
            if self._content(note_id)[1] != base_edit_seq:
                raise Conflict()
        self.save_draft(note_id, rev["content_md"], base_edit_seq)
        return self.checkpoint(note_id, kind="restore", force=True)

    def hard_delete(self, note_id: str) -> list[str]:
        """Delete only explicitly trashed notes. Media paths returned for post-commit cleanup."""
        with self.transaction():
            n = self._note(note_id)
            if not n["trashed_at"]:
                raise AppError("INVALID", "Move note to Trash before permanently deleting")
            media = [row["file_relpath"] for row in self._all(
                "SELECT file_relpath FROM recordings WHERE note_id=? AND file_relpath IS NOT NULL", (note_id,))]
            self.db.execute("DELETE FROM recordings WHERE note_id=?", (note_id,))
            # Avoid ON DELETE SET NULL on immutable revisions. Delete descendants
            # first, then parents; the trigger remains in force during the purge.
            self.db.execute("UPDATE notes SET current_revision_id=NULL WHERE id=?", (note_id,))
            self.db.execute("DELETE FROM note_drafts WHERE note_id=?", (note_id,))
            revisions = self._all("SELECT id,parent_revision_id FROM note_revisions WHERE note_id=?", (note_id,))
            remaining = {r["id"]: r["parent_revision_id"] for r in revisions}
            while remaining:
                referenced_parents = {parent for parent in remaining.values() if parent}
                leaves = [rev for rev in remaining if rev not in referenced_parents]
                if not leaves:
                    raise AppError("INTEGRITY", "Revision graph is not acyclic")
                for revision_id in leaves:
                    self.db.execute("DELETE FROM note_revisions WHERE id=?", (revision_id,))
                    del remaining[revision_id]
            self.db.execute("DELETE FROM notes WHERE id=?", (note_id,))
            return media

    # Recordings / jobs / quick captures -------------------------------------------------
    def capture_started(self, mode: str, note_id: str | None = None) -> dict:
        if mode not in ("note", "quick"):
            raise AppError("INVALID", "Unknown recording mode")
        ident, t = identifier(), now()
        with self.transaction():
            if note_id:
                self._note(note_id)
            self.db.execute("INSERT INTO recordings(id,note_id,mode,status,spool_name,created_at) "
                            "VALUES(?,?,?,'capturing',?,?)", (ident, note_id, mode, ident + ".part", t))
            if mode == "quick":
                self.db.execute("INSERT INTO quick_items(id,recording_id,status,created_at,updated_at) "
                                "VALUES(? , ?,'processing',?,?)", (ident, ident, t, t))
        return {"recording_id": ident, "quick_id": ident if mode == "quick" else None}

    def capture_finalized(self, recording_id: str, info: dict, *, status: str = "ready") -> None:
        if status not in ("ready", "incomplete", "error"):
            raise AppError("INVALID", "Invalid capture completion state")
        with self.transaction():
            self.db.execute("UPDATE recordings SET status=?,file_relpath=?,duration_ms=?,size_bytes=?,"
                            "content_sha256=?,error_code=?,finalized_at=? WHERE id=? AND status='capturing'",
                            (status, info.get("file_relpath"), info.get("duration_ms"), info.get("size_bytes"),
                             info.get("content_sha256"), info.get("error_code"), now(), recording_id))
            if status != "ready":
                self.db.execute("UPDATE quick_items SET status='error',updated_at=? WHERE recording_id=?",
                                (now(), recording_id))

    def recording(self, recording_id: str) -> dict:
        with self.lock:
            result = self._one("SELECT * FROM recordings WHERE id=?", (recording_id,))
            if not result:
                raise AppError("NOT_FOUND", "Recording not found")
            return result

    def recordings(self, *, note_id: str | None = None, recoverable: bool = False) -> list[dict]:
        with self.lock:
            if note_id:
                return self._all("SELECT r.*, t.original_text, t.id AS transcript_id FROM recordings r "
                                 "LEFT JOIN transcripts t ON t.recording_id=r.id "
                                 "WHERE note_id=? ORDER BY r.created_at DESC LIMIT 100", (note_id,))
            if recoverable:
                return self._all("SELECT r.*, t.original_text FROM recordings r LEFT JOIN transcripts t "
                                 "ON t.recording_id=r.id WHERE r.status IN ('error','incomplete','ready') "
                                 "AND t.id IS NULL ORDER BY r.created_at DESC LIMIT 100")
            return self._all("SELECT * FROM recordings ORDER BY created_at DESC LIMIT 100")

    def set_recording_error(self, recording_id: str, code: str) -> None:
        with self.transaction():
            self.db.execute("UPDATE recordings SET status='error',error_code=? WHERE id=?", (code, recording_id))
            self.db.execute("UPDATE quick_items SET status='error',updated_at=? WHERE recording_id=?",
                            (now(), recording_id))

    def create_job(self, operation: str, source_id: str, model: str, retry_of: str | None = None,
                   idempotency_key: str | None = None) -> dict:
        ident, t = identifier(), now()
        with self.transaction():
            self.db.execute("INSERT INTO jobs(id,operation,source_id,idempotency_key,retry_of_job_id,"
                            "status,provider_model,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,'queued',?,?,?)",
                            (ident, operation, source_id, idempotency_key or ident, retry_of, model, t, t))
        return {"id": ident, "status": "queued"}

    def job(self, job_id: str) -> dict:
        with self.lock:
            result = self._one("SELECT * FROM jobs WHERE id=?", (job_id,))
            if not result:
                raise AppError("NOT_FOUND", "Job not found")
            return result

    def job_status(self, job_id: str, status: str, error_code: str | None = None) -> None:
        if status not in ("queued", "running", "succeeded", "failed", "cancelled", "interrupted"):
            raise ValueError("invalid job status")
        with self.transaction():
            self.db.execute("UPDATE jobs SET status=?,error_code=?,updated_at=? WHERE id=?",
                            (status, error_code, now(), job_id))

    def transcript_saved(self, recording_id: str, text: str, model: str, language: str | None = None,
                         retention_hours: int = 24) -> dict:
        if not text.strip() or len(text) > 500000:
            raise AppError("PROVIDER_OUTPUT", "Transcription is empty or too large")
        ident, t = identifier(), now()
        with self.transaction():
            self.db.execute("INSERT INTO transcripts(id,recording_id,original_text,language,provider_model,created_at) "
                            "VALUES(?,?,?,?,?,?)", (ident, recording_id, text, language, model, t))
            quick = self._one("SELECT id FROM quick_items WHERE recording_id=?", (recording_id,))
            if quick:
                expiry = (datetime.now(timezone.utc) + timedelta(hours=retention_hours)).isoformat() if retention_hours >= 0 else None
                self.db.execute("UPDATE quick_items SET transcript_id=?,edited_text=?,status='ready',expires_at=?,"
                                "updated_at=? WHERE recording_id=?", (ident, text, expiry, t, recording_id))
        return {"id": ident, "original_text": text}

    def quick(self, quick_id: str | None = None) -> dict | None:
        with self.lock:
            sql = ("SELECT q.*, t.original_text, r.status AS recording_status "
                   "FROM quick_items q LEFT JOIN transcripts t ON t.id=q.transcript_id "
                   "JOIN recordings r ON r.id=q.recording_id ")
            if quick_id:
                return self._one(sql + "WHERE q.id=?", (quick_id,))
            return self._one(sql + "ORDER BY q.created_at DESC LIMIT 1")

    def edit_quick(self, quick_id: str, text: str) -> dict:
        if len(text) > 500000:
            raise AppError("TOO_LARGE", "Quick output is too long")
        with self.transaction():
            q = self.quick(quick_id)
            if not q or q["status"] not in ("ready", "delivered"):
                raise AppError("INVALID", "Quick text is not ready")
            self.db.execute("UPDATE quick_items SET edited_text=?,updated_at=? WHERE id=?",
                            (text, now(), quick_id))
        return self.quick(quick_id)

    def mark_quick(self, quick_id: str, state: str) -> None:
        if state not in ("processing", "ready", "delivered", "error", "cancelled"):
            raise AppError("INVALID", "Invalid quick status")
        with self.transaction():
            self.db.execute("UPDATE quick_items SET status=?,updated_at=? WHERE id=?", (state, now(), quick_id))

    def delivery(self, quick_id: str, attempt_id: str, fingerprint: str, clipboard: str,
                 paste: str, target_hint: str | None = None) -> dict:
        with self.transaction():
            exists = self._one("SELECT * FROM delivery_attempts WHERE id=?", (attempt_id,))
            if exists:
                return exists
            self.db.execute("INSERT INTO delivery_attempts(id,quick_item_id,content_sha256,"
                            "clipboard_status,paste_status,target_hint,created_at) VALUES(?,?,?,?,?,?,?)",
                            (attempt_id, quick_id, fingerprint, clipboard, paste, target_hint, now()))
        return self._one("SELECT * FROM delivery_attempts WHERE id=?", (attempt_id,))

    def update_delivery(self, attempt_id: str, *, clipboard: str | None = None,
                        paste: str | None = None) -> None:
        if clipboard is not None and clipboard not in ("not_attempted", "copied", "failed"):
            raise AppError("INVALID", "Unknown clipboard status")
        if paste is not None and paste not in ("not_attempted", "sent", "blocked", "unknown"):
            raise AppError("INVALID", "Unknown paste status")
        with self.transaction():
            if not self._one("SELECT id FROM delivery_attempts WHERE id=?", (attempt_id,)):
                raise AppError("NOT_FOUND", "Delivery attempt not found")
            if clipboard is not None:
                self.db.execute("UPDATE delivery_attempts SET clipboard_status=? WHERE id=?",
                                (clipboard, attempt_id))
            if paste is not None:
                self.db.execute("UPDATE delivery_attempts SET paste_status=? WHERE id=?",
                                (paste, attempt_id))

    def recent_deliveries(self, quick_id: str) -> list[dict]:
        with self.lock:
            return self._all("SELECT * FROM delivery_attempts WHERE quick_item_id=? "
                             "ORDER BY created_at DESC LIMIT 20", (quick_id,))

    def save_quick_as_note(self, quick_id: str) -> dict:
        q = self.quick(quick_id)
        if not q or not q["edited_text"]:
            raise AppError("INVALID", "There is no quick transcription to save")
        if q["saved_note_id"]:
            return self.get_note(q["saved_note_id"])
        note = self.create_note(title="Voice note", content=q["edited_text"], kind="transcript")
        with self.transaction():
            self.db.execute("UPDATE quick_items SET saved_note_id=? WHERE id=?", (note["id"], quick_id))
            self.db.execute("UPDATE recordings SET note_id=? WHERE id=?", (note["id"], q["recording_id"]))
        return note

    # AI proposals ------------------------------------------------------------------------
    def proposal(self, proposal_id: str) -> dict:
        with self.lock:
            p = self._one("SELECT * FROM ai_proposals WHERE id=?", (proposal_id,))
            if not p:
                raise AppError("NOT_FOUND", "AI proposal not found")
            return p

    def proposals(self, note_id: str) -> list[dict]:
        with self.lock:
            return self._all("SELECT id,status,preset_id,created_at,source_edit_seq FROM ai_proposals "
                             "WHERE source_note_id=? ORDER BY created_at DESC LIMIT 30", (note_id,))

    def proposal_saved(self, job_id: str, note_id: str | None, source_revision_id: str | None,
                       edit_seq: int | None, source_hash: str, preset_id: str, instruction: str,
                       model: str, output: str) -> dict:
        ident, t = identifier(), now()
        with self.transaction():
            self.db.execute("INSERT INTO ai_proposals(id,job_id,source_note_id,source_revision_id,"
                            "source_edit_seq,source_sha256,preset_id,preset_instruction_snapshot,"
                            "provider_model,proposed_content_md,status,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                            (ident, job_id, note_id, source_revision_id, edit_seq, source_hash,
                             preset_id, instruction, model, output, t, t))
        return self.proposal(ident)

    def discard_proposal(self, proposal_id: str) -> None:
        with self.transaction():
            p = self.proposal(proposal_id)
            if p["status"] == "pending":
                self.db.execute("UPDATE ai_proposals SET status='discarded',updated_at=? WHERE id=?",
                                (now(), proposal_id))

    def accept_proposal_status(self, proposal_id: str) -> None:
        with self.transaction():
            p = self.proposal(proposal_id)
            if p["status"] != "pending":
                raise AppError("INVALID", "AI proposal was already resolved")
            self.db.execute("UPDATE ai_proposals SET status='accepted',updated_at=? WHERE id=?",
                            (now(), proposal_id))

    def apply_proposal(self, proposal_id: str, action: str) -> dict:
        if action not in ("current", "alternative", "new-note"):
            raise AppError("INVALID", "Unknown proposal action")
        p = self.proposal(proposal_id)
        if p["status"] != "pending":
            raise AppError("INVALID", "AI proposal was already resolved")
        note_id = p["source_note_id"]
        if action == "new-note" or note_id is None:
            note = self.create_note(title="AI: " + p["preset_id"], content=p["proposed_content_md"])
            with self.transaction():
                self.db.execute("UPDATE ai_proposals SET status='accepted',updated_at=? WHERE id=?",
                                (now(), proposal_id))
            return {"note_id": note["id"], "revision_id": note["current_revision_id"]}
        with self.transaction():
            n = self._note(note_id)
            body, seq, _ = self._content(note_id)
            if action == "current" and (seq != p["source_edit_seq"] or digest(body) != p["source_sha256"]):
                raise Conflict("The note changed after this AI request. Save as alternative or new note.")
            current_id = n["current_revision_id"]
            revision_id = identifier()
            self.db.execute("INSERT INTO note_revisions(id,note_id,parent_revision_id,kind,content_md,"
                            "source_sha256,origin_job_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (revision_id, note_id, current_id, "ai_accepted", p["proposed_content_md"],
                             p["source_sha256"], p["job_id"], now()))
            if action == "current":
                self.db.execute("UPDATE notes SET current_revision_id=?,updated_at=? WHERE id=?",
                                (revision_id, now(), note_id))
                self.db.execute("INSERT INTO note_drafts(note_id,base_revision_id,content_md,edit_seq,updated_at) "
                                "VALUES(?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET "
                                "base_revision_id=excluded.base_revision_id,content_md=excluded.content_md,"
                                "edit_seq=excluded.edit_seq,updated_at=excluded.updated_at",
                                (note_id, revision_id, p["proposed_content_md"], seq+1, now()))
                self._index(note_id, n["title"], p["proposed_content_md"])
            self.db.execute("UPDATE ai_proposals SET status='accepted',updated_at=? WHERE id=?",
                            (now(), proposal_id))
        return {"note_id": note_id, "revision_id": revision_id, "selected": action == "current"}

    def append_transcript(self, recording_id: str, expected_edit_seq: int | None = None) -> dict:
        """Append at most once, atomically, with optional capture-time CAS."""
        with self.transaction():
            r = self.recording(recording_id)
            note_id = r["note_id"]
            if not note_id:
                raise AppError("INVALID", "Recording has no note")
            t = self._one("SELECT original_text FROM transcripts WHERE recording_id=?", (recording_id,))
            if not t:
                raise AppError("INVALID", "Recording is not transcribed")
            if self._one("SELECT recording_id FROM note_recording_applies WHERE recording_id=?", (recording_id,)):
                return {"note_id": note_id, "applied": False, "already_applied": True}
            n = self._note(note_id)
            if n["trashed_at"]:
                raise AppError("TRASHED", "Restore note before appending recording")
            body, seq, base = self._content(note_id)
            if expected_edit_seq is not None and seq != expected_edit_seq:
                raise Conflict("Note changed while recording; append only when explicitly requested")
            result = body.rstrip() + ("\n\n" if body.strip() else "") + t["original_text"]
            self.db.execute("INSERT INTO note_drafts(note_id,base_revision_id,content_md,edit_seq,updated_at) "
                            "VALUES(?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET "
                            "content_md=excluded.content_md,edit_seq=excluded.edit_seq,updated_at=excluded.updated_at",
                            (note_id, base, result, seq+1, now()))
            self.db.execute("INSERT INTO note_recording_applies(recording_id,note_id,applied_at) VALUES(?,?,?)",
                            (recording_id, note_id, now()))
            self.db.execute("UPDATE notes SET updated_at=? WHERE id=?", (now(), note_id))
            self._index(note_id, n["title"], result)
            return {"note_id": note_id, "applied": True, "edit_seq": seq + 1}

    def salvage_recording(self, recording_id: str, info: dict) -> dict:
        with self.transaction():
            r = self.recording(recording_id)
            if r["status"] not in ("incomplete", "error"):
                raise AppError("INVALID", "Recording does not need salvaging")
            self.db.execute("UPDATE recordings SET status='ready',file_relpath=?,duration_ms=?,"
                            "size_bytes=?,content_sha256=?,error_code=NULL,finalized_at=? WHERE id=?",
                            (info["file_relpath"], info["duration_ms"], info["size_bytes"],
                             info["content_sha256"], now(), recording_id))
            return self.recording(recording_id)

    def recover_startup(self) -> dict:
        """No automatic provider replay; stop orphaned operations and mark for user recovery."""
        with self.transaction():
            self.db.execute("UPDATE jobs SET status='interrupted',error_code='SERVICE_RESTART',"
                            "updated_at=? WHERE status IN ('queued','running')", (now(),))
            self.db.execute("UPDATE recordings SET status='incomplete',error_code='SERVICE_RESTART' "
                            "WHERE status='capturing'")
            self.db.execute("UPDATE quick_items SET status='error',updated_at=? "
                            "WHERE status='processing'", (now(),))
            missing = self._all("SELECT id,file_relpath FROM recordings WHERE file_relpath IS NOT NULL "
                                "AND status IN ('ready','error','incomplete')")
            for row in missing:
                try:
                    exists = self.paths.media_path(row["file_relpath"]).is_file()
                except ValueError:
                    exists = False
                if not exists:
                    self.db.execute("UPDATE recordings SET status='error',error_code='MEDIA_MISSING' WHERE id=?",
                                    (row["id"],))
        return {"recovery_pending": len(self.recordings(recoverable=True))}

    def expire_quick(self) -> list[str]:
        """Expired quick rows are deleted only if they are completed, never unresolved audio."""
        with self.transaction():
            expired = self._all("SELECT q.id,r.id recording_id,r.file_relpath,r.status "
                                "FROM quick_items q JOIN recordings r ON r.id=q.recording_id "
                                "WHERE q.expires_at IS NOT NULL AND q.expires_at<? "
                                "AND q.status IN ('delivered','ready')", (now(),))
            remove = []
            for item in expired:
                if item["status"] not in ("ready", "deleted"):
                    continue
                if self._one("SELECT id FROM notes WHERE id=(SELECT saved_note_id FROM quick_items WHERE id=?)",
                             (item["id"],)):
                    continue
                if item["file_relpath"]:
                    remove.append(item["file_relpath"])
                self.db.execute("DELETE FROM recordings WHERE id=?", (item["recording_id"],))
            return remove

    def integrity(self) -> dict:
        with self.lock:
            return {"database": self.db.execute("PRAGMA integrity_check").fetchone()[0],
                    "foreign_keys": len(self.db.execute("PRAGMA foreign_key_check").fetchall()),
                    "fts": bool(self.search_enabled),
                    "notes": self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
                    "recordings": self.db.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]}

    def backup_db(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            # closing(): sqlite3's own context manager only commits; an open
            # handle keeps the file locked on Windows (temp cleanup fails).
            with closing(sqlite3.connect(destination)) as target:
                self.db.backup(target)
