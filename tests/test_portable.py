import json
import sqlite3
import zipfile

import pytest

from dictation4me.errors import AppError
from dictation4me.portable import backup, export_markdown, restore
from dictation4me.storage import Notebook


def test_backup_restore_and_integrity(app_paths, tmp_path):
    db = Notebook(app_paths)
    db.create_note("Demo: žlutý", "# Hello\nSome real text 📝")
    file = tmp_path / "backup.zip"
    result = backup(db, file)
    assert result["unencrypted"] is True
    destination = tmp_path / "restored"
    restored = restore(file, destination)
    assert restored["files"] >= 1
    with sqlite3.connect(destination / "data/notes.sqlite3") as saved:
        assert saved.execute("SELECT title FROM notes").fetchone()[0] == "Demo: žlutý"
    with pytest.raises(AppError):
        restore(file, destination)
    db.close()


def test_markdown_export_no_secrets(app_paths, tmp_path):
    db = Notebook(app_paths)
    db.create_note("Tech Design", "**Architectural** choice")
    out = tmp_path / "markdown.zip"
    export_markdown(db, out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "manifest.json" in names
        assert any(name.endswith(".md") for name in names)
        assert "config.toml" not in names
        md = z.read(next(x for x in names if x.endswith(".md"))).decode()
        assert md == "**Architectural** choice"
    db.close()


def test_restore_reject_path_traversal(app_paths, tmp_path):
    zippath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zippath, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "dictation4me-v2", "kind": "native-backup",
                                                   "schema": 2, "files": [{"path": "../bad", "size": 1, "sha256": "x"}]}))
        z.writestr("../bad", b"x")
    with pytest.raises(AppError):
        restore(zippath, tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()


def test_restore_reject_tampered_hash(app_paths, tmp_path):
    db = Notebook(app_paths)
    db.create_note("one", "two")
    normal = tmp_path / "good.zip"
    backup(db, normal)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(normal) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "db/notes.sqlite3":
                data = b"X" + data[1:]
            target.writestr(name, data)
    with pytest.raises(AppError):
        restore(tampered, tmp_path / "fresh")
    db.close()
