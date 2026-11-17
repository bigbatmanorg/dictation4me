-- Dictation4me 2.0 specification: executable SQLite schema baseline, migration 1.
-- A real application MUST implement transactional migrations, backups and service-side
-- invariants described in docs/05_DATA.md. This file is a contract, not a deployed DB.
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA journal_mode = WAL;
BEGIN TRANSACTION;

CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY CHECK(version > 0),
  applied_at TEXT NOT NULL,
  description TEXT NOT NULL
);

CREATE TABLE folders (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE notes (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT 'Untitled' CHECK(length(title) <= 500),
  folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
  current_revision_id TEXT REFERENCES note_revisions(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
  pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
  archived_at TEXT,
  trashed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE tags (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(name) BETWEEN 1 AND 120)
);

CREATE TABLE note_tags (
  note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(note_id, tag_id)
);

CREATE TABLE note_revisions (
  id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  parent_revision_id TEXT REFERENCES note_revisions(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK(kind IN ('initial','manual','transcript','ai_accepted','restore','import')),
  content_md TEXT NOT NULL,
  source_sha256 TEXT,
  origin_job_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE note_drafts (
  note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
  base_revision_id TEXT REFERENCES note_revisions(id) ON DELETE SET NULL,
  content_md TEXT NOT NULL DEFAULT '',
  edit_seq INTEGER NOT NULL DEFAULT 0 CHECK(edit_seq >= 0),
  updated_at TEXT NOT NULL
);

CREATE TABLE recordings (
  id TEXT PRIMARY KEY,
  note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,
  mode TEXT NOT NULL CHECK(mode IN ('note','quick')),
  status TEXT NOT NULL CHECK(status IN ('capturing','incomplete','ready','error','deleted')),
  file_relpath TEXT UNIQUE,
  spool_name TEXT,
  mime_type TEXT NOT NULL DEFAULT 'audio/wav',
  duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
  size_bytes INTEGER CHECK(size_bytes IS NULL OR size_bytes >= 0),
  content_sha256 TEXT,
  error_code TEXT,
  retain_until TEXT,
  created_at TEXT NOT NULL,
  finalized_at TEXT
);

CREATE TABLE transcripts (
  id TEXT PRIMARY KEY,
  recording_id TEXT NOT NULL UNIQUE REFERENCES recordings(id) ON DELETE CASCADE,
  original_text TEXT NOT NULL,
  language TEXT,
  provider_model TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  operation TEXT NOT NULL CHECK(operation IN ('stt','ai')),
  source_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  retry_of_job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
  generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
  attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
  status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled','interrupted')),
  provider_model TEXT NOT NULL,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE ai_proposals (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
  source_note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,
  source_revision_id TEXT REFERENCES note_revisions(id) ON DELETE SET NULL,
  source_edit_seq INTEGER CHECK(source_edit_seq IS NULL OR source_edit_seq >= 0),
  source_sha256 TEXT NOT NULL,
  preset_id TEXT NOT NULL,
  preset_instruction_snapshot TEXT NOT NULL,
  provider_model TEXT NOT NULL,
  proposed_content_md TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','accepted','discarded','superseded')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE quick_items (
  id TEXT PRIMARY KEY,
  recording_id TEXT NOT NULL UNIQUE REFERENCES recordings(id) ON DELETE CASCADE,
  transcript_id TEXT REFERENCES transcripts(id) ON DELETE SET NULL,
  edited_text TEXT,
  status TEXT NOT NULL CHECK(status IN ('processing','ready','delivered','error','cancelled')),
  saved_note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE delivery_attempts (
  id TEXT PRIMARY KEY,
  quick_item_id TEXT NOT NULL REFERENCES quick_items(id) ON DELETE CASCADE,
  content_sha256 TEXT NOT NULL,
  clipboard_status TEXT NOT NULL CHECK(clipboard_status IN ('not_attempted','copied','failed')),
  paste_status TEXT NOT NULL CHECK(paste_status IN ('not_attempted','sent','blocked','unknown')),
  target_hint TEXT,
  created_at TEXT NOT NULL
);

-- Search projection is the canonical current note title/body copy used by FTS.
-- Service upserts it transactionally when title or current draft/revision changes.
CREATE TABLE note_search_docs (
  rowid INTEGER PRIMARY KEY,
  note_id TEXT NOT NULL UNIQUE REFERENCES notes(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  body TEXT NOT NULL
);
CREATE VIRTUAL TABLE note_search_fts USING fts5(
  title, body, content='note_search_docs', content_rowid='rowid', tokenize='unicode61'
);
CREATE TRIGGER note_search_docs_ai AFTER INSERT ON note_search_docs BEGIN
  INSERT INTO note_search_fts(rowid, title, body) VALUES(new.rowid, new.title, new.body);
END;
CREATE TRIGGER note_search_docs_ad AFTER DELETE ON note_search_docs BEGIN
  INSERT INTO note_search_fts(note_search_fts, rowid, title, body)
    VALUES('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER note_search_docs_au AFTER UPDATE ON note_search_docs BEGIN
  INSERT INTO note_search_fts(note_search_fts, rowid, title, body)
    VALUES('delete', old.rowid, old.title, old.body);
  INSERT INTO note_search_fts(rowid, title, body) VALUES(new.rowid, new.title, new.body);
END;

CREATE TRIGGER notes_current_revision_validate
BEFORE UPDATE OF current_revision_id ON notes
WHEN NEW.current_revision_id IS NOT NULL
 AND NOT EXISTS (
  SELECT 1 FROM note_revisions r WHERE r.id = NEW.current_revision_id AND r.note_id = NEW.id
)
BEGIN
 SELECT RAISE(ABORT, 'current_revision_id must belong to the same note');
END;

-- Accepted snapshots are immutable. Deletion is allowed only by explicit trash purge
-- and occurs through a controlled service transaction, never via a content update.
CREATE TRIGGER note_revisions_immutable BEFORE UPDATE ON note_revisions BEGIN
 SELECT RAISE(ABORT, 'accepted revision is immutable');
END;
CREATE TRIGGER transcripts_immutable BEFORE UPDATE ON transcripts BEGIN
 SELECT RAISE(ABORT, 'original transcript is immutable');
END;
CREATE TRIGGER note_revisions_parent_validate
BEFORE INSERT ON note_revisions
WHEN NEW.parent_revision_id IS NOT NULL
 AND NOT EXISTS (
  SELECT 1 FROM note_revisions p WHERE p.id = NEW.parent_revision_id AND p.note_id = NEW.note_id
)
BEGIN
 SELECT RAISE(ABORT, 'parent revision must belong to the same note');
END;

CREATE INDEX notes_updated_idx ON notes(updated_at DESC);
CREATE INDEX notes_folder_idx ON notes(folder_id, trashed_at, updated_at DESC);
CREATE INDEX notes_trashed_idx ON notes(trashed_at);
CREATE INDEX note_revisions_note_idx ON note_revisions(note_id, created_at DESC);
CREATE INDEX recordings_note_idx ON recordings(note_id, created_at DESC);
CREATE INDEX recordings_status_idx ON recordings(status, created_at DESC);
CREATE INDEX jobs_status_idx ON jobs(status, created_at DESC);
CREATE INDEX quick_items_expiry_idx ON quick_items(expires_at, status);
CREATE INDEX proposals_note_idx ON ai_proposals(source_note_id, created_at DESC);
CREATE INDEX deliveries_quick_idx ON delivery_attempts(quick_item_id, created_at DESC);

INSERT INTO schema_migrations(version, applied_at, description)
 VALUES(1, '2026-11-16T00:00:00Z', 'Initial clean-slate notebook and dictation schema');
COMMIT;
