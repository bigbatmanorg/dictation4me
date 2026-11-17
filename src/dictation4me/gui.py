"""NativeHTMLKit notes GUI using only public SDK window/document APIs. No JavaScript."""
from __future__ import annotations

import html
import re
import threading
import time
import uuid
from importlib.resources import files
from pathlib import Path

from .errors import AppError
from .ipc import invoke
from .markdown import render
from .paths import Paths


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def option(value: str, label: str) -> str:
    return f'<option value="{esc(value)}">{esc(label)}</option>'


class NotebookUI:
    def __init__(self, app, window, paths: Paths, initial_tab: str = "notes"):
        self.app = app
        self.window = window
        self.doc = window.document
        self.paths = paths
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.selected_note: str | None = None
        self.note_seq = 0
        self.dirty = False
        self.quick_dirty = False
        self.quick_id: str | None = None
        self.quick_text_last = None
        self.current_scope = "active"
        self.current_folder = ""
        self.search = ""
        self.page = 0
        self.selected_proposal: str | None = None
        self.pending_deletion: tuple[str, str] | None = None
        self.quick_proposal: str | None = None
        self.pending_job: dict | None = None
        self.preview_on = False
        self.current_tab = "notes"
        self.timer: threading.Timer | None = None
        self.quick_timer: threading.Timer | None = None
        self.list_count = 0
        self.presets: list[dict] = []
        self.folders: list[dict] = []
        self.last_state = ""
        self.last_note_refresh = 0.0
        self.doc.on("click", self._click)
        self.doc.on("input", self._input)
        self.doc.on("change", self._change)
        self.doc.on("keydown", self._keydown)
        for key in ("n", "s", "k"):
            self.doc.prevent_default("shell", "keydown", key=key, ctrl=True)
        window.on_before_close(self._before_close)
        app.on_shutdown(self.stop)
        self.refresh_presets()
        self.refresh_folders()
        self.refresh_notes()
        # First launch is immediately writable. Never present an editable but
        # unbound textarea whose contents cannot be autosaved.
        if not self.selected_note:
            notes = self.call("notes.list", limit=1)
            if notes:
                self.load_note(notes[0]["id"])
            else:
                note = self.call("notes.create", title="Untitled")
                self.load_note(note["id"])
        self.load_settings()
        self.show_tab(initial_tab)
        self.thread = threading.Thread(target=self._poll, daemon=True, name="dictation-ui-poll")
        self.thread.start()

    def call(self, action: str, **params):
        timeout = params.pop("_timeout", 10)
        return invoke(self.paths, action, params, timeout=timeout)

    def notice(self, message: str, *, is_error: bool = False):
        if self.window.closed:
            return
        self.doc.set_text("status-message", message[:280])
        if is_error:
            self.doc.set_text("save-status", "Needs attention")

    def _safe(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AppError as exc:
            self.notice(f"{exc.code}: {exc}", is_error=True)
        except Exception:
            # UI exceptions cannot include raw provider URLs, credentials or note text.
            self._log_exception()
            self.notice("An application action failed; details in state/gui.log", is_error=True)
        return None

    def _log_exception(self):
        """Exception type and stack only (no locals, values or message text)."""
        import sys
        import traceback
        try:
            kind, _value, tb = sys.exc_info()
            frames = "".join(traceback.format_tb(tb)) if tb else ""
            with (self.paths.state / "gui.log").open("a", encoding="utf-8") as log:
                log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {kind.__name__ if kind else '?'}\n{frames}\n")
        except OSError:
            pass

    def settings_feedback(self, message: str, *, is_error: bool = False):
        if self.window.closed:
            return
        self.doc.set_text("settings-feedback", message[:400])
        if is_error:
            self.doc.add_class("settings-feedback", "error")
        else:
            self.doc.remove_class("settings-feedback", "error")

    def _poll(self):
        while not self.stop_event.wait(.7):
            if self.window.closed:
                break
            try:
                status = self.call("status")
                with self.lock:
                    state = status["state"]
                    if self.last_state != state:
                        self.last_state = state
                        self.doc.set_text("connection-status", state.title())
                        self.doc.set_text("quick-state", state.title())
                        self.doc.set_text("capture-title", {
                            "RECORDING": "Recording in progress", "TRANSCRIBING": "Transcribing audio...",
                            "TRANSFORMING": "Applying AI rewrite...", "READY": "Transcription ready",
                            "RECOVERABLE_RESULT": "Output delivered", "ERROR": "Recovery required"}.get(
                            state, "Ready to record"))
                        self.doc.set_text("quick-feedback", status.get("error") or
                                          "Clipboard copy can be verified; insertion into other apps cannot.")
                        if self.current_tab == "recovery":
                            self.refresh_recovery()
                    self.doc.set_text("capture-detail", "Shortcut: " + self._current_shortcut())
                    hotkey = status.get("hotkey") or "unknown"
                    if hotkey != getattr(self, "last_hotkey", None):
                        self.last_hotkey = hotkey
                        self.doc.set_text("cfg-hotkey-status", "Shortcut status: " + hotkey)
                    if self.current_tab == "quick" and not self.quick_dirty:
                        self.refresh_quick()
                    if self.pending_job:
                        self._poll_job()
                    if (self.current_tab == "notes" and self.selected_note and not self.dirty
                        and time.monotonic() - self.last_note_refresh > 3):
                        self._refresh_note_if_changed()
                        self.last_note_refresh = time.monotonic()
            except (AppError, OSError, RuntimeError):
                with self.lock:
                    if not self.window.closed:
                        self.doc.set_text("connection-status", "Disconnected")
                        self.last_state = ""  # redraw state labels after reconnect

    def _current_shortcut(self) -> str:
        try:
            return self.settings["quick"]["shortcut"]
        except (AttributeError, KeyError):
            return "Ctrl+Alt+D"

    def stop(self):
        self.stop_event.set()
        for timer in (self.timer, self.quick_timer):
            if timer:
                timer.cancel()

    def _before_close(self) -> bool:
        with self.lock:
            return self.flush() and self.flush_quick()

    def _schedule(self, kind: str):
        attr = "timer" if kind == "note" else "quick_timer"
        old = getattr(self, attr)
        if old:
            old.cancel()
        timer = threading.Timer(.65, self._timer_flush, args=(kind,))
        timer.daemon = True
        setattr(self, attr, timer)
        timer.start()

    def _timer_flush(self, kind: str):
        with self.lock:
            if self.stop_event.is_set() or self.window.closed:
                return
            if kind == "note":
                self.flush()
            else:
                self.flush_quick()

    def flush(self) -> bool:
        """Autosave only after the service acknowledges commit. Keep dirty text on failure."""
        with self.lock:
            if not self.dirty or not self.selected_note:
                return True
            try:
                source = self.doc.get_value("note-editor")
                answer = self.call("notes.draft", id=self.selected_note, text=source,
                                   base_edit_seq=self.note_seq)
                self.note_seq = answer["edit_seq"]
                self.dirty = False
                self.doc.set_text("save-status", "Saved locally")
                self.doc.set_text("note-wordcount", f"{len(source.split())} words")
                return True
            except AppError as exc:
                self.notice(f"Save FAILED [{exc.code}] — text remains in editor", is_error=True)
                return False

    def flush_quick(self) -> bool:
        with self.lock:
            if not self.quick_dirty or not self.quick_id:
                return True
            try:
                text = self.doc.get_value("quick-editor")
                self.call("quick.edit", id=self.quick_id, text=text)
                self.quick_dirty = False
                self.quick_text_last = text
                self.notice("Quick dictation edits saved")
                return True
            except AppError as exc:
                self.notice(f"Quick text save FAILED [{exc.code}]", is_error=True)
                return False

    def _input(self, event):
        ident = event.get("element", "")
        with self.lock:
            if ident == "note-editor":
                self.dirty = True
                self.doc.set_text("save-status", "Saving...")
                self._schedule("note")
            elif ident == "quick-editor":
                self.quick_dirty = True
                self._schedule("quick")
            elif ident == "search-input":
                self.search = self.doc.get_value("search-input")[:200]
                self.page = 0
                self.refresh_notes()

    def _change(self, event):
        ident = event.get("element", "")
        with self.lock:
            if ident == "scope-select":
                self.current_scope = self.doc.get_value(ident)
                self.page = 0
                self.refresh_notes()
            elif ident == "folder-filter":
                self.current_folder = self.doc.get_value(ident)
                self.page = 0
                self.refresh_notes()
            elif ident in ("note-title", "note-folder", "note-tags") and self.selected_note:
                self._safe(self._save_metadata)
            elif ident == "cfg-preset-choice":
                self._safe(self._load_preset_editor)

    def _keydown(self, event):
        if not event.get("ctrl") or event.get("alt") or event.get("meta"):
            return
        key = str(event.get("key", "")).lower()
        with self.lock:
            if key == "n":
                self._safe(self.new_note)
            elif key == "s":
                self._safe(self.checkpoint)
            elif key == "k":
                self._safe(self.show_tab, "notes")
                self.doc.focus("search-input")

    def _click(self, event):
        # When clicking nested text/HTML inside a note button, the host reports
        # the nearest id (or an inner id). Follow its documented bubbling path.
        path = event.get("path") or [event.get("element", "")]
        with self.lock:
            for ident in path:
                if ident.startswith(("entry--", "revision-view--", "revision-restore--",
                                     "proposal-select--", "recording-play--", "recording-append--",
                                     "recover-retry--", "recover-delete--", "recover-salvage--",
                                     "nav-", "btn-")):
                    self._safe(self._dispatch, ident)
                    break

    def _dispatch(self, ident: str):
        if ident.startswith("entry--"):
            return self.load_note(ident[len("entry--"):])
        if ident.startswith("revision-view--"):
            return self.view_revision(ident[len("revision-view--"):])
        if ident.startswith("revision-restore--"):
            return self.restore_revision(ident[len("revision-restore--"):])
        if ident.startswith("proposal-select--"):
            return self.select_proposal(ident[len("proposal-select--"):])
        if ident.startswith("recording-play--"):
            self.call("recordings.play", id=ident[len("recording-play--"):])
            return self.notice("Playing recording...")
        if ident.startswith("recording-append--"):
            if not self.flush():
                return
            self.call("recordings.append", id=ident[len("recording-append--"):])
            return self.reload_current()
        if ident.startswith("recover-retry--"):
            self.call("recordings.retry", id=ident[len("recover-retry--"):])
            return self.refresh_recovery()
        if ident.startswith("recover-delete--"):
            return self.ask_delete("recording", ident[len("recover-delete--"):])
        if ident.startswith("recover-salvage--"):
            self.call("recordings.salvage", id=ident[len("recover-salvage--"):])
            return self.refresh_recovery()
        if ident.startswith("nav-"):
            return self.show_tab(ident[4:])
        actions = {
            "btn-new": self.new_note, "btn-prev": lambda: self.turn_page(-1),
            "btn-next": lambda: self.turn_page(1), "btn-folder-add": self.create_folder,
            "btn-checkpoint": self.checkpoint, "btn-preview": self.toggle_preview,
            "btn-show-ai": self.toggle_ai_panel, "btn-record-note": self.record_note,
            "btn-stop-note": self.stop_recording, "btn-copy-note": self.copy_note,
            "btn-pin": lambda: self.change_flag("pinned"),
            "btn-archive": lambda: self.change_flag("archived"),
            "btn-trash": lambda: self.change_flag("trashed"),
            "btn-delete-permanently": lambda: self.ask_delete("note", self.selected_note),
            "btn-delete-cancel": self.cancel_delete,
            "btn-delete-confirm": self.confirm_delete,
            "btn-transform-note": self.transform_note,
            "btn-ai-close": self.toggle_ai_panel,
            "btn-accept-current": lambda: self.accept_proposal("current"),
            "btn-accept-alternative": lambda: self.accept_proposal("alternative"),
            "btn-accept-new-note": lambda: self.accept_proposal("new-note"),
            "btn-discard-proposal": self.discard_proposal,
            "btn-quick-record": self.quick_record, "btn-quick-stop": self.stop_recording,
            "btn-quick-cancel": lambda: self.call("capture.cancel"),
            "btn-quick-copy": self.quick_copy, "btn-quick-deliver": self.quick_deliver,
            "btn-quick-save": self.flush_quick, "btn-quick-promote": self.quick_promote,
            "btn-quick-transform": self.transform_quick, "btn-quick-accept-ai": self.accept_quick_ai,
            "btn-refresh-recovery": self.refresh_recovery,
            "btn-save-settings": self.save_settings, "btn-test-ai": self.test_ai,
            "btn-test-stt": self.test_stt,
            "btn-preset-new": self.new_preset, "btn-preset-save": self.save_preset,
            "btn-preset-delete": self.delete_preset,
        }
        func = actions.get(ident)
        if func:
            return func()

    def show_tab(self, tab: str):
        if tab not in ("notes", "quick", "recovery", "settings"):
            return
        if not self.flush() or not self.flush_quick():
            return
        for name in ("notes", "quick", "recovery", "settings"):
            if name == tab:
                self.doc.remove_class(name + "-panel", "hidden")
                self.doc.add_class("nav-" + name, "active")
            else:
                self.doc.add_class(name + "-panel", "hidden")
                self.doc.remove_class("nav-" + name, "active")
        self.current_tab = tab
        if tab == "quick":
            self.refresh_quick(force=True)
        if tab == "recovery":
            self.refresh_recovery()
        if tab == "settings":
            self.load_settings()

    def refresh_notes(self):
        items = self.call("notes.list", search=self.search, scope=self.current_scope,
                          folder_id=self.current_folder or None, limit=41, offset=self.page * 40)
        self.list_count = len(items)
        visible = items[:40]
        result = []
        for n in visible:
            active = " selected" if n["id"] == self.selected_note else ""
            star = "★ " if n["pinned"] else ""
            result.append(f'<button id="entry--{esc(n["id"])}" class="note-entry{active}">'
                          f'<div class="entry-title">{star}{esc(n["title"])}</div>'
                          f'<div class="entry-excerpt">{esc(n["excerpt"] or "")}</div>'
                          f'<div class="entry-meta">{esc(n["updated_at"][:16].replace("T", " "))}</div></button>')
        self.doc.replace_html("note-items", "".join(result) if result else
                              '<div class="empty-message">No notes found. Create a new one.</div>')
        self.doc.set_text("page-number", str(self.page + 1))
        self.doc.set_text("footer-count", f"{len(visible)} shown")

    def turn_page(self, direction: int):
        if direction > 0 and self.list_count <= 40:
            return
        self.page = max(0, self.page + direction)
        self.refresh_notes()

    def refresh_folders(self):
        self.folders = self.call("folders.list")
        self.doc.replace_html("folder-filter", option("", "All folders") +
                              "".join(option(f["id"], f["name"]) for f in self.folders))
        self.doc.replace_html("note-folder", option("", "No folder") +
                              "".join(option(f["id"], f["name"]) for f in self.folders))

    def create_folder(self):
        name = self.doc.get_value("new-folder-name").strip()
        if not name:
            return
        self.call("folders.create", name=name)
        self.doc.set_value("new-folder-name", "")
        self.refresh_folders()
        self.notice("Folder created")

    def new_note(self):
        if not self.flush():
            return
        note = self.call("notes.create", title="Untitled")
        self.current_scope = "active"
        self.doc.set_value("scope-select", "active")
        self.load_note(note["id"])
        self.refresh_notes()

    def load_note(self, note_id: str):
        if not self.flush():
            return
        n = self.call("notes.get", id=note_id)
        self.selected_note = n["id"]
        self.note_seq = n["edit_seq"]
        self.dirty = False
        self.doc.set_value("note-title", n["title"])
        self.doc.set_value("note-editor", n["content_md"])
        self.doc.set_value("note-tags", ", ".join(n["tags"]))
        self.doc.set_value("note-folder", n["folder_id"] or "")
        self.doc.set_text("current-note-path", n["title"])
        self.doc.set_text("note-wordcount", f"{len(n['content_md'].split())} words")
        self.doc.set_text("save-status", "Saved locally")
        self.doc.set_text("btn-pin", "★ Pinned" if n["pinned"] else "☆ Pin")
        self.doc.set_text("btn-archive", "Unarchive" if n["archived_at"] else "Archive")
        self.doc.set_text("btn-trash", "Restore" if n["trashed_at"] else "Trash")
        if n["trashed_at"]:
            self.doc.remove_class("btn-delete-permanently", "hidden")
        else:
            self.doc.add_class("btn-delete-permanently", "hidden")
        self.doc.replace_html("note-preview", render(n["content_md"]))
        self.refresh_notes()
        self.refresh_revisions()
        self.refresh_note_recordings()
        self.doc.set_value("proposal-text", "")
        self.doc.set_text("proposal-meta", "No proposal selected")
        self.selected_proposal = None

    def reload_current(self):
        if self.selected_note:
            self.dirty = False  # only called after an acknowledged persisted operation
            self.load_note(self.selected_note)

    def _refresh_note_if_changed(self):
        if not self.selected_note:
            return
        n = self.call("notes.get", id=self.selected_note)
        if n["edit_seq"] != self.note_seq and not self.dirty:
            self.load_note(self.selected_note)

    def _save_metadata(self):
        if not self.selected_note or not self.flush():
            return
        title = self.doc.get_value("note-title")[:500]
        folder_id = self.doc.get_value("note-folder") or None
        tags = [x.strip() for x in self.doc.get_value("note-tags").split(",") if x.strip()]
        self.call("notes.fields", id=self.selected_note,
                  fields={"title": title, "folder_id": folder_id, "tags": tags})
        self.doc.set_text("current-note-path", title or "Untitled")
        self.refresh_notes()

    def change_flag(self, flag: str):
        if not self.selected_note or not self.flush():
            return
        n = self.call("notes.get", id=self.selected_note)
        value = bool(n["pinned"]) if flag == "pinned" else bool(n[flag + "_at"])
        self.call("notes.fields", id=self.selected_note, fields={flag: not value})
        self.reload_current()
        self.notice(flag.title() + " updated")

    def ask_delete(self, kind: str, identifier: str | None):
        if not identifier or not self.flush() or not self.flush_quick():
            return
        if kind == "note" and not self.call("notes.get", id=identifier)["trashed_at"]:
            return self.notice("Move the note to Trash before deleting permanently", is_error=True)
        self.pending_deletion = (kind, identifier)
        self.doc.set_text("delete-title", "Delete note permanently?" if kind == "note" else "Discard recording?")
        self.doc.set_text("delete-description",
                          "All revisions and attached recordings will be erased permanently."
                          if kind == "note" else
                          "Recording audio and recovery data will be discarded permanently.")
        self.doc.show_dialog("confirm-deletion")

    def cancel_delete(self):
        self.pending_deletion = None
        self.doc.close_dialog("confirm-deletion")

    def confirm_delete(self):
        operation = self.pending_deletion
        if not operation:
            return
        kind, identifier = operation
        self.doc.close_dialog("confirm-deletion")
        self.pending_deletion = None
        if kind == "recording":
            self.call("recordings.delete", id=identifier)
            self.refresh_recovery()
            self.notice("Recovery recording discarded")
        else:
            self.call("notes.purge", id=identifier)
            self.selected_note = None
            self.note_seq = 0
            self.dirty = False
            first = self.call("notes.list", scope=self.current_scope, limit=1)
            if not first:
                self.current_scope = "active"
                self.doc.set_value("scope-select", "active")
                first = self.call("notes.list", scope="active", limit=1)
            if first:
                self.load_note(first[0]["id"])
            else:
                self.new_note()
            self.refresh_notes()
            self.notice("Note deleted permanently")

    def checkpoint(self):
        if self.selected_note and self.flush():
            result = self.call("notes.checkpoint", id=self.selected_note)
            self.refresh_revisions()
            self.notice("Revision saved" if result["created"] else "Already up to date")

    def toggle_preview(self):
        if self.preview_on:
            self.doc.add_class("note-preview", "hidden")
            self.doc.remove_class("note-editor", "hidden")
        else:
            body = self.doc.get_value("note-editor")
            self.doc.replace_html("note-preview", render(body))
            self.doc.remove_class("note-preview", "hidden")
            self.doc.add_class("note-editor", "hidden")
        self.preview_on = not self.preview_on

    def toggle_ai_panel(self):
        # On desktop the inspector is part of the 3-column layout; at narrower
        # widths the same panel becomes a dismissible drawer (CSS media query).
        if getattr(self, "ai_drawer_open", False):
            self.doc.remove_class("ai-panel", "drawer-open")
            self.ai_drawer_open = False
        else:
            self.doc.add_class("ai-panel", "drawer-open")
            self.ai_drawer_open = True

    def copy_note(self):
        if self.selected_note:
            self.app.set_clipboard_text(self.doc.get_value("note-editor"))
            self.notice("Note copied to clipboard")

    def refresh_revisions(self):
        if not self.selected_note:
            return
        result = []
        for revision in self.call("notes.revisions", id=self.selected_note):
            ident = esc(revision["id"])
            result.append('<div class="compact-row"><span>' +
                          esc(revision["kind"]) + " · " + esc(revision["created_at"][0:16]) +
                          f'</span><button id="revision-view--{ident}">View</button>'
                          f'<button id="revision-restore--{ident}">Restore</button></div>')
        self.doc.replace_html("revisions-list", "".join(result) or '<div class="hint">No revisions</div>')
        proposals = self.call("ai.proposals", note_id=self.selected_note)
        self.doc.replace_html("proposals-list", "".join(
            '<div class="compact-row"><span>' + esc(p["preset_id"]) + " · " + esc(p["status"]) +
            f'</span><button id="proposal-select--{esc(p["id"])}">View</button></div>' for p in proposals)
            or '<div class="hint">No AI history</div>')

    def view_revision(self, revision_id: str):
        rev = self.call("notes.revision", id=revision_id)
        if rev["note_id"] != self.selected_note:
            return
        self.selected_proposal = None
        self.doc.set_value("proposal-text", rev["content_md"])
        self.doc.set_text("proposal-meta", "Revision preview · Select Restore in history to use it")

    def restore_revision(self, revision_id: str):
        if self.selected_note and self.flush():
            self.call("notes.restore", note_id=self.selected_note, revision_id=revision_id,
                      base_edit_seq=self.note_seq)
            self.reload_current()
            self.notice("Previous revision restored as a new revision")

    def refresh_note_recordings(self):
        if not self.selected_note:
            return
        rows = self.call("recordings.note", note_id=self.selected_note)
        result = []
        for r in rows:
            ident = esc(r["id"])
            details = f'{esc(r["created_at"][:16])} · {esc(r["status"])} · {int(r["duration_ms"] or 0) // 1000}s'
            buttons = (f'<button id="recording-play--{ident}">Play</button>' if r["file_relpath"] else "")
            if r["original_text"]:
                buttons += f'<button id="recording-append--{ident}">Append text</button>'
            result.append(f'<div class="compact-row"><span>{details}</span>{buttons}</div>')
        self.doc.replace_html("note-recordings", "".join(result) or '<span class="hint">No recordings attached</span>')

    def refresh_presets(self):
        self.presets = self.call("presets.list")
        options = option("", "Choose a preset...") + "".join(
            option(p["id"], p["name"]) for p in self.presets)
        self.doc.replace_html("preset-select", options)
        self.doc.replace_html("quick-preset", options)
        self.doc.replace_html("cfg-preset-choice", options)
        self.doc.replace_html("cfg-auto-preset", option("", "Disabled") + "".join(
            option(p["id"], p["name"]) for p in self.presets))

    def transform_note(self):
        if not self.selected_note or not self.flush():
            return
        preset_id = self.doc.get_value("preset-select")
        if not preset_id:
            return self.notice("Select an AI preset first")
        result = self.call("ai.note", note_id=self.selected_note, preset_id=preset_id)
        self.pending_job = {"id": result["job_id"], "kind": "note"}
        self.doc.set_text("proposal-meta", "AI request running. You can keep editing the note.")
        self.notice("AI transform submitted")

    def _poll_job(self):
        task = self.pending_job
        if not task:
            return
        result = self.call("ai.job", id=task["id"])
        state = result["job"]["status"]
        if state in ("queued", "running"):
            return
        self.pending_job = None
        if state != "succeeded" or not result.get("proposal"):
            self.notice("AI operation failed or was interrupted: " + str(result["job"].get("error_code")), is_error=True)
            return
        p = result["proposal"]
        if task["kind"] == "note":
            self.selected_proposal = p["id"]
            self.doc.set_value("proposal-text", p["proposed_content_md"])
            self.doc.set_text("proposal-meta", "Proposal ready: " + p["preset_id"] +
                              ". Review before accepting.")
            self.refresh_revisions()
        else:
            self.quick_proposal = p["id"]
            self.doc.set_value("quick-ai-text", p["proposed_content_md"])
            self.notice("Quick AI proposal ready")

    def select_proposal(self, proposal_id: str):
        p = self.call("ai.proposal", id=proposal_id)
        self.selected_proposal = p["id"] if p["status"] == "pending" else None
        self.doc.set_value("proposal-text", p["proposed_content_md"])
        self.doc.set_text("proposal-meta", "AI: " + p["preset_id"] + " · " + p["status"])

    def accept_proposal(self, action: str):
        if not self.selected_proposal or not self.flush():
            return self.notice("Select a pending AI proposal first")
        result = self.call("ai.accept", id=self.selected_proposal, action=action)
        self.selected_proposal = None
        if action == "current" and result["note_id"] == self.selected_note:
            self.reload_current()
        elif action == "new-note":
            self.load_note(result["note_id"])
        self.refresh_revisions()
        self.notice("AI proposal saved as " + action)

    def discard_proposal(self):
        if self.selected_proposal:
            self.call("ai.discard", id=self.selected_proposal)
            self.selected_proposal = None
            self.doc.set_value("proposal-text", "")
            self.doc.set_text("proposal-meta", "Discarded")
            self.refresh_revisions()

    def record_note(self):
        if self.selected_note and self.flush():
            self.call("capture.start", mode="note", note_id=self.selected_note)
            self.notice("Recording for this note. Press Stop when finished.")

    def stop_recording(self):
        result = self.call("capture.stop")
        self.notice("Recording stopped: " + result["state"])
        self.refresh_note_recordings()

    def quick_record(self):
        self.call("capture.start", mode="quick")
        self.notice("Quick recording started")

    def refresh_quick(self, *, force=False):
        q = self.call("quick.get")
        if not q:
            return
        if (force or self.quick_id != q["id"] or self.quick_text_last != q["edited_text"]) and not self.quick_dirty:
            self.quick_id = q["id"]
            self.quick_text_last = q["edited_text"]
            self.doc.set_value("quick-editor", q["edited_text"] or "")

    def quick_copy(self):
        if self.flush_quick():
            result = self.call("quick.copy")
            self.notice("Quick text copied to clipboard")

    def quick_deliver(self):
        if self.flush_quick():
            result = self.call("quick.deliver")
            self.doc.set_text("quick-feedback", "Copied. Paste shortcut: " + result["paste_status"] +
                              ". Target acceptance is unknown.")

    def quick_promote(self):
        if self.flush_quick() and self.quick_id:
            n = self.call("quick.save-note", id=self.quick_id)
            self.show_tab("notes")
            self.load_note(n["id"])
            self.notice("Saved quick dictation as note")

    def transform_quick(self):
        if not self.flush_quick() or not self.quick_id:
            return
        preset = self.doc.get_value("quick-preset")
        if not preset:
            return self.notice("Choose an AI preset first")
        result = self.call("ai.quick", quick_id=self.quick_id, preset_id=preset)
        self.pending_job = {"id": result["job_id"], "kind": "quick"}
        self.notice("Quick AI transform running")

    def accept_quick_ai(self):
        if not self.quick_id or not self.quick_proposal or not self.flush_quick():
            return
        self.call("ai.accept-quick", quick_id=self.quick_id, id=self.quick_proposal)
        self.quick_proposal = None
        self.refresh_quick(force=True)
        self.notice("Quick AI proposal applied; original transcript retained")

    def refresh_recovery(self):
        items = self.call("recordings.recovery")
        result = []
        for r in items:
            ident = esc(r["id"])
            result.append('<div class="recovery-card"><div class="heading">' + esc(r["mode"].title()) +
                          ' recording · ' + esc(r["status"]) + '</div><div class="smallnote">'
                          + esc(r["created_at"][:19]) + ' · ' + esc(r["error_code"] or "Unprocessed") +
                          ' · ' + str(r["size_bytes"] or 0) + ' bytes</div><div class="action-row">' +
                          (f'<button id="recover-retry--{ident}">Retry STT</button>' if r["file_relpath"] else
                           f'<button id="recover-salvage--{ident}">Salvage audio</button>') +
                          f'<button id="recover-delete--{ident}" class="danger">Discard</button>' +
                          '</div></div>')
        self.doc.replace_html("recovery-items", "".join(result) or
                              '<div class="empty-message">No failed or incomplete recordings.</div>')

    def load_settings(self):
        self.settings = self.call("config.get")
        for prefix in ("stt", "ai"):
            cfg = self.settings[prefix]
            for short, key in (("url", "base_url"), ("path", "path"), ("model", "model"),
                               ("source", "key_source"), ("ref", "key_reference")):
                self.doc.set_value(f"cfg-{prefix}-{short}", cfg[key])
        self.doc.set_value("cfg-stt-profile", self.settings["stt"]["request_profile"])
        self.doc.set_value("cfg-shortcut", self.settings["quick"]["shortcut"])
        self.doc.set_value("cfg-output", self.settings["quick"]["output_mode"])
        self.doc.set_value("cfg-paste-shortcut", self.settings["quick"]["paste_shortcut"])
        self.doc.set_value("cfg-auto-preset", self.settings["ai"]["automatic_quick_preset_id"])
        self.doc.set_value("cfg-retention", str(self.settings["quick"]["quick_text_retention_hours"]))
        self.doc.set_checked("cfg-auto-deliver", self.settings["quick"]["auto_deliver"])
        self.doc.set_checked("cfg-unsafe-wayland", self.settings["quick"]["paste_to_current_focus_unsafe_wayland"])
        self.doc.set_checked("cfg-retain-quick-audio", self.settings["quick"]["record_audio_success_retention"] == "keep")
        self.doc.set_checked("cfg-remote-approved", self.settings["privacy"]["remote_provider_approved"])
        try:
            devices = self.call("audio.devices")
        except AppError:
            devices = []
        self.doc.replace_html("cfg-audio-device", option("default", "System default") + "".join(
            option(str(d["id"]), d["name"]) for d in devices))
        self.doc.set_value("cfg-audio-device", str(self.settings["audio"]["device"]))

    def save_settings(self):
        changes = {}
        secrets = {}
        for section in ("stt", "ai"):
            changes[section] = {
                "base_url": self.doc.get_value(f"cfg-{section}-url").strip(),
                "path": self.doc.get_value(f"cfg-{section}-path").strip(),
                "model": self.doc.get_value(f"cfg-{section}-model").strip(),
                "key_source": self.doc.get_value(f"cfg-{section}-source"),
                "key_reference": self.doc.get_value(f"cfg-{section}-ref").strip()}
            secret = self.doc.get_value(f"cfg-{section}-secret")
            if secret:
                secrets[section] = secret
        changes["stt"]["request_profile"] = self.doc.get_value("cfg-stt-profile")
        changes["ai"]["automatic_quick_preset_id"] = self.doc.get_value("cfg-auto-preset")
        changes["audio"] = {"device": self.doc.get_value("cfg-audio-device")}
        changes["privacy"] = {
            "remote_provider_approved": bool(self.doc.get_checked("cfg-remote-approved"))}
        changes["quick"] = {
            "record_audio_success_retention": ("keep" if self.doc.get_checked("cfg-retain-quick-audio")
                                                else "delete_after_transcription"),
            "shortcut": self.doc.get_value("cfg-shortcut").strip(),
            "output_mode": self.doc.get_value("cfg-output"),
            "paste_shortcut": self.doc.get_value("cfg-paste-shortcut"),
            "quick_text_retention_hours": int(self.doc.get_value("cfg-retention")),
            "auto_deliver": bool(self.doc.get_checked("cfg-auto-deliver")),
            "paste_to_current_focus_unsafe_wayland": bool(self.doc.get_checked("cfg-unsafe-wayland"))}
        try:
            result = self.call("config.save", changes=changes, secrets=secrets)
        except AppError as exc:
            # Nothing was written: the service validates the whole change set first.
            self.settings_feedback(f"Not saved — {exc}", is_error=True)
            raise
        for section in ("stt", "ai"):
            self.doc.set_value(f"cfg-{section}-secret", "")
        self.settings = result["settings"]
        for section in ("stt", "ai"):
            self.doc.set_value(f"cfg-{section}-source", self.settings[section]["key_source"])
            self.doc.set_value(f"cfg-{section}-ref", self.settings[section]["key_reference"])
        self.doc.set_value("cfg-shortcut", self.settings["quick"]["shortcut"])
        message = "Settings saved"
        if result.get("shortcut_changed"):
            message += ". Shortcut rebound — " + str(result.get("hotkey", ""))
        self.settings_feedback(message + time.strftime(" (%H:%M:%S)"))
        self.notice(message)

    def _load_preset_editor(self):
        ident = self.doc.get_value("cfg-preset-choice")
        preset = next((p for p in self.presets if p["id"] == ident), None)
        self.doc.set_value("cfg-preset-name", preset["name"] if preset else "")
        self.doc.set_value("cfg-preset-prompt", preset["instruction"] if preset else "")

    def new_preset(self):
        self.doc.set_value("cfg-preset-choice", "")
        self.doc.set_value("cfg-preset-name", "")
        self.doc.set_value("cfg-preset-prompt", "")
        self.notice("Enter a new preset name and instruction, then Save preset")

    def save_preset(self):
        name = self.doc.get_value("cfg-preset-name").strip()
        instr = self.doc.get_value("cfg-preset-prompt").strip()
        ident = self.doc.get_value("cfg-preset-choice")
        if not ident:
            ident = re.sub(r"[^a-z0-9-]", "-", name.lower().replace(" ", "-"))[:70].strip("-") + "-" + uuid.uuid4().hex[:5]
        self.call("presets.save", preset={"id": ident, "name": name, "description": "Custom preset",
                                          "instruction": instr, "preserve_language": True,
                                          "output_format": "markdown"})
        self.refresh_presets()
        self.doc.set_value("cfg-preset-choice", ident)
        self.notice("Preset saved")

    def delete_preset(self):
        ident = self.doc.get_value("cfg-preset-choice")
        if ident:
            self.call("presets.delete", id=ident)
            self.refresh_presets()
            self.new_preset()
            self.notice("Preset deleted")

    def test_ai(self):
        self.save_settings()
        self.notice("Testing AI endpoint...")
        self.settings_feedback("Testing AI endpoint...")

        def work():
            try:
                result = self.call("provider.test-ai", _timeout=130)
                if not self.stop_event.is_set():
                    self.notice("AI endpoint responded: " + result["status"])
                    self.settings_feedback("AI test OK: model replied (" + str(result.get("characters")) + " chars)")
            except AppError as exc:
                if not self.stop_event.is_set():
                    self.notice("AI test failed [" + exc.code + "]", is_error=True)
                    self.settings_feedback(f"AI test failed [{exc.code}]: {exc}", is_error=True)
        threading.Thread(target=work, name="dictation-ai-test", daemon=True).start()

    def test_stt(self):
        self.save_settings()
        # Explicit native picker; no file/recording is sent without this action.
        future = self.window.open_files(multiple=False, title="Select test WAV audio",
                                        filters=[{"name": "WAV audio", "extensions": ["wav"]}], wait=False)
        def selected(fut):
            try:
                value = fut.result()
                if not value["cancelled"] and value["paths"]:
                    self.call("provider.test-stt", file=value["paths"][0], _timeout=130)
                    self.notice("STT sample transcribed successfully")
                    self.settings_feedback("STT test OK: sample transcribed")
            except AppError as exc:
                self.notice(f"STT test failed [{exc.code}]", is_error=True)
                self.settings_feedback(f"STT test failed [{exc.code}]: {exc}", is_error=True)
            except Exception:
                self.notice("STT file dialog failed", is_error=True)
        future.add_done_callback(selected)


def main(*, initial_tab: str = "notes") -> int:
    try:
        from nativehtmlkit import Application
    except ImportError as exc:
        raise AppError("GUI_UNAVAILABLE", "Install nativehtmlkit (Python SDK) for the notebook UI") from exc
    # Application.start may reexecute the script. There are no service/file/network
    # side effects before this call; initialize only in the backend role.
    with Application.start(name="Dictation4me", app_id="org.bigbatmanorg.dictation4me") as app:
        from .cli import ensure_service
        paths = Paths.discover()
        ensure_service(paths)
        asset = Path(str(files("dictation4me").joinpath("assets/notebook.html")))
        window = app.open_file(asset, title="Dictation4me — Notes & Dictation", width=1340, height=850,
                               min_width=700, min_height=520)
        ui = NotebookUI(app, window, paths, initial_tab=initial_tab)
        app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
