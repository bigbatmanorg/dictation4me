"""Private, portable application paths. DICTATION4ME_HOME isolates tests and agents."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


def ensure_private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def atomic_bytes(path: Path, content: bytes) -> None:
    ensure_private(path.parent)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_text(path: Path, content: str) -> None:
    atomic_bytes(path, content.encode("utf-8"))


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    state: Path
    runtime: Path

    @classmethod
    def discover(cls) -> Paths:
        home = os.environ.get("DICTATION4ME_HOME", "").strip()
        if home:
            root = Path(home).expanduser().resolve()
            return cls(root / "config", root / "data", root / "state", root / "runtime")
        if sys.platform == "win32":
            roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
            local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
            return cls(roaming / "Dictation4me", local / "Dictation4me/data",
                       local / "Dictation4me/state", local / "Dictation4me/run")
        base = Path.home()
        config = Path(os.environ.get("XDG_CONFIG_HOME", base / ".config"))
        data = Path(os.environ.get("XDG_DATA_HOME", base / ".local/share"))
        state = Path(os.environ.get("XDG_STATE_HOME", base / ".local/state"))
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", state))
        return cls(config / "dictation4me", data / "dictation4me",
                   state / "dictation4me", runtime / "dictation4me")

    def init(self) -> Paths:
        for p in (self.config, self.data, self.state, self.runtime, self.audio, self.spool):
            ensure_private(p)
        return self

    @property
    def database(self) -> Path:
        return self.data / "notes.sqlite3"

    @property
    def audio(self) -> Path:
        return self.data / "audio"

    @property
    def spool(self) -> Path:
        return self.audio / "spool"

    @property
    def config_file(self) -> Path:
        return self.config / "config.toml"

    @property
    def presets_file(self) -> Path:
        return self.config / "presets.json"

    @property
    def endpoint_file(self) -> Path:
        return self.runtime / "control.json"

    @property
    def lock_file(self) -> Path:
        return self.runtime / "instance.lock"

    def media_path(self, rel: str) -> Path:
        """Fail closed against path traversal, absolute paths, and escaping audio root."""
        candidate = (self.data / rel).resolve()
        if candidate == self.audio.resolve() or self.audio.resolve() not in candidate.parents:
            raise ValueError("Audio path is outside private audio directory")
        return candidate
