"""Simple reusable prompt presets, stored in private JSON (no plugin registry)."""
from __future__ import annotations

import json
import threading
from importlib.resources import files

from .errors import AppError
from .paths import Paths, atomic_text


class Presets:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.lock = threading.RLock()
        if not paths.presets_file.is_file():
            default = files("dictation4me").joinpath("assets/presets.json").read_text("utf-8")
            atomic_text(paths.presets_file, default)

    def list(self) -> list[dict]:
        with self.lock:
            data = json.loads(self.paths.presets_file.read_text(encoding="utf-8"))
        return data["presets"]

    def get(self, ident: str) -> dict:
        for preset in self.list():
            if preset["id"] == ident:
                return preset
        raise AppError("NOT_FOUND", "Preset not found")

    def save(self, preset: dict) -> dict:
        if not isinstance(preset, dict):
            raise AppError("INVALID", "Preset must be an object")
        ident = preset.get("id")
        name = preset.get("name")
        instruction = preset.get("instruction")
        if (not isinstance(ident, str) or len(ident) > 80 or
            not ident.replace("-", "").replace("_", "").isalnum() or
            not isinstance(name, str) or not 1 <= len(name) <= 100 or
            not isinstance(instruction, str) or not 1 <= len(instruction) <= 5000):
            raise AppError("INVALID", "Invalid preset ID, name, or instruction")
        value = {"id": ident, "name": name, "description": str(preset.get("description", ""))[:250],
                 "instruction": instruction,
                 "output_format": preset.get("output_format", "plain"),
                 "preserve_language": bool(preset.get("preserve_language", True))}
        if value["output_format"] not in ("plain", "markdown"):
            raise AppError("INVALID", "Preset format must be plain or markdown")
        with self.lock:
            items = self.list()
            if not any(p["id"] == ident for p in items):
                if len(items) >= 100:
                    raise AppError("LIMIT", "Maximum 100 AI presets")
                items.append(value)
            else:
                items = [value if p["id"] == ident else p for p in items]
            self._write(items)
        return value

    def delete(self, ident: str) -> None:
        with self.lock:
            items = self.list()
            self._write([p for p in items if p["id"] != ident])

    def _write(self, items: list[dict]) -> None:
        atomic_text(self.paths.presets_file,
                    json.dumps({"schema_version": 1, "presets": items}, ensure_ascii=False, indent=2))
