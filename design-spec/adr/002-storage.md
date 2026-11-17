# ADR-002: SQLite and durable audio spool

**Status:** accepted.

**Context:** Simple Markdown files alone do not atomically represent versions, drafts, tags, AI jobs, recovery and search. Memory-only audio loses speech on crash.

**Decision:** One service writer to SQLite WAL; separate private audio files with bounded streaming spool and fsync/atomic finalize. Notes have autosaved drafts, immutable revisions, search projection with FTS5 and safe backup/restore. Failed audio remains recoverable.

**Alternatives rejected:** external Postgres/Redis, vector DB, one-file-per-note as primary storage, memory-only spool.

**Consequences:** need transactional migrations and media garbage collection. Portable Markdown export avoids lock-in.
