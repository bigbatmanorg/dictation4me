"""Portable Markdown export, SQLite backup with audio and safe restore-to-new-home."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .errors import AppError
from .storage import Notebook

MAGIC = "dictation4me-v2"
MAX_RESTORE_SIZE = 4 * 1024**3
MAX_RESTORE_ENTRIES = 100000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-.")[:80] or "note"


def _write_zip(output: Path, root: Path, *, format_name: str, metadata: dict,
               overwrite: bool = False) -> dict:
    if output.exists() and not overwrite:
        raise AppError("EXISTS", "Backup destination already exists; specify a different filename")
    if output.suffix.lower() != ".zip":
        raise AppError("INVALID", "Output filename must end with .zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            entries.append({"path": rel, "sha256": sha256(p), "size": p.stat().st_size})
    manifest = {"format": MAGIC, "kind": format_name, "schema": 2,
                "created_at": datetime.now(timezone.utc).isoformat(), "files": entries,
                "metadata": metadata, "encrypted": False}
    with tempfile.NamedTemporaryFile(suffix=".part", prefix=".dictation4me-",
                                     dir=output.parent, delete=False) as tmp:
        temp_name = Path(tmp.name)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for entry in entries:
                z.write(root / entry["path"], entry["path"])
        os.replace(temp_name, output)
    finally:
        temp_name.unlink(missing_ok=True)
    return {"output": str(output), "file_count": len(entries), "kind": format_name,
            "unencrypted": True}


def backup(store: Notebook, output: Path, with_audio: bool = True, *, overwrite: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="dictation4me-backup-") as temp:
        root = Path(temp)
        dbfile = root / "db/notes.sqlite3"
        store.backup_db(dbfile)
        if with_audio:
            with store.lock:
                refs = [r[0] for r in store.db.execute(
                    "SELECT file_relpath FROM recordings WHERE file_relpath IS NOT NULL")]
            for rel in refs:
                try:
                    source = store.paths.media_path(rel)
                except ValueError:
                    continue
                if source.is_file() and not source.is_symlink():
                    destination = root / rel
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
        return _write_zip(output, root, format_name="native-backup",
                          metadata={"includes_audio": with_audio}, overwrite=overwrite)


def export_markdown(store: Notebook, output: Path, *, with_audio: bool = False,
                    overwrite: bool = False) -> dict:
    notes_metadata = []
    with tempfile.TemporaryDirectory(prefix="dictation4me-export-") as temp:
        root = Path(temp)
        offset = 0
        while True:
            items = store.list_notes(scope="all", limit=100, offset=offset)
            if not items:
                break
            for summary in items:
                note = store.get_note(summary["id"])
                if note["trashed_at"]:
                    continue
                filename = f"notes/{note['id']}-{safe_name(note['title'])}.md"
                destination = root / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(note["content_md"], encoding="utf-8")
                notes_metadata.append({"id": note["id"], "title": note["title"], "path": filename,
                                       "folder_id": note["folder_id"], "tags": note["tags"],
                                       "pinned": bool(note["pinned"]), "archived": bool(note["archived_at"])})
                if with_audio:
                    for rec in store.recordings(note_id=note["id"]):
                        if rec["file_relpath"]:
                            source = store.paths.media_path(rec["file_relpath"])
                            if source.is_file() and not source.is_symlink():
                                out = root / rec["file_relpath"]
                                out.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(source, out)
            offset += len(items)
        return _write_zip(output, root, format_name="markdown-export",
                          metadata={"notes": notes_metadata, "includes_audio": with_audio},
                          overwrite=overwrite)


def _safe_zip_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return (name and not name.startswith("/") and "\\" not in name and
            not any(x in ("", "..", ".") for x in path.parts) and
            not re.match(r"^[A-Za-z]:", name) and
            name != "manifest.json")


def restore(archive: Path, destination: Path) -> dict:
    """Verify every file/hash before atomic installation into an initially absent home."""
    if destination.exists():
        raise AppError("EXISTS", "Destination must be a new, nonexistent directory")
    if not archive.is_file():
        raise AppError("NOT_FOUND", "Backup archive does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dictation4me-restore-", dir=destination.parent) as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(archive) as z:
            try:
                meta = json.loads(z.read("manifest.json"))
            except (KeyError, ValueError) as exc:
                raise AppError("INVALID_BACKUP", "Archive lacks a valid manifest") from exc
            if meta.get("format") != MAGIC or meta.get("kind") != "native-backup" or meta.get("schema") not in (1, 2):
                raise AppError("INVALID_BACKUP", "Only compatible Dictation4me native backups can be restored")
            entries = meta.get("files")
            if not isinstance(entries, list) or len(entries) > MAX_RESTORE_ENTRIES:
                raise AppError("INVALID_BACKUP", "Invalid backup entry list")
            expected = {}
            total = 0
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise AppError("INVALID_BACKUP", "Malformed backup entry")
                path = entry["path"]
                if not _safe_zip_entry(path) or not (path == "db/notes.sqlite3" or path.startswith("audio/")):
                    raise AppError("INVALID_BACKUP", "Archive contains an unsafe path")
                if path in expected or not isinstance(entry.get("size"), int) or entry["size"] < 0:
                    raise AppError("INVALID_BACKUP", "Duplicate or invalid backup entry")
                total += entry["size"]
                if total > MAX_RESTORE_SIZE:
                    raise AppError("INVALID_BACKUP", "Backup exceeds restore size limit")
                expected[path] = entry
            if "db/notes.sqlite3" not in expected:
                raise AppError("INVALID_BACKUP", "Backup has no database")
            names = set(z.namelist())
            if names != set(expected) | {"manifest.json"}:
                raise AppError("INVALID_BACKUP", "Archive entries differ from its manifest")
            for path, entry in expected.items():
                info = z.getinfo(path)
                if info.is_dir() or info.file_size != entry["size"]:
                    raise AppError("INVALID_BACKUP", "Backup file size does not match manifest")
                # Reject Unix symbolic links embedded in the ZIP.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise AppError("INVALID_BACKUP", "Symlinks are not accepted in backup")
                output = (root / ("data/" + path[3:] if path.startswith("db/") else "data/" + path)).resolve()
                if root.resolve() not in output.parents:
                    raise AppError("INVALID_BACKUP", "Archive path escapes destination")
                output.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                with z.open(info) as inp, output.open("wb") as out:
                    while chunk := inp.read(1024 * 1024):
                        h.update(chunk)
                        out.write(chunk)
                if h.hexdigest() != entry["sha256"]:
                    raise AppError("INVALID_BACKUP", "Backup content hash mismatch")
        # Prevent installation of a corrupt/unsupported database.
        import sqlite3
        dbfile = root / "data/notes.sqlite3"
        with sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AppError("INVALID_BACKUP", "Backup SQLite integrity check failed")
        # TemporaryDirectory cleans only its original name; atomically renamed folder persists.
        os.replace(root, destination)
        if os.name != "nt":
            destination.chmod(0o700)
    return {"restored_to": str(destination), "files": len(expected),
            "note": "Settings/API credentials were not included; configure them in the new profile"}
