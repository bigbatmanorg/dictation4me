"""NativeHTMLKit notes GUI using only public SDK window/document APIs. No JavaScript."""
from __future__ import annotations

import html
import re
import sys
import threading
import time
import traceback
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


TABS = ("notes", "quick", "recovery", "settings")
SETTING_TABS = ("connections", "dictation", "presets", "privacy")
SCOPES = ("active", "archive", "trash")
BUSY = ("FINALIZING", "TRANSCRIBING", "TRANSFORMING", "DELIVERING")
STATE_CLASSES = ("state-idle", "state-recording", "state-busy", "state-ready", "state-error",
                 "state-offline")

# One table drives the sidebar card and the Quick page: state -> presentation.
# (css class, title, hint, primary action label, primary action)
PRESENTATION = {
    "IDLE": ("state-idle", "Ready", "Press {key} anywhere to start dictating.",
             "Start dictation", "start"),
    "CANCELLED": ("state-idle", "Ready", "Press {key} anywhere to start dictating.",
                  "Start dictation", "start"),
    "RECORDING": ("state-recording", "Recording…", "Speak now. Press {key} again to stop.",
                  "Stop & transcribe", "stop"),
    "FINALIZING": ("state-busy", "Finishing…", "Saving the recording.", "Working…", None),
    "TRANSCRIBING": ("state-busy", "Transcribing…", "Turning your speech into text.",
                     "Working…", None),
    "TRANSFORMING": ("state-busy", "Rewriting…", "Applying your automatic AI rewrite.",
                     "Working…", None),
    "DELIVERING": ("state-busy", "Copying…", "Copying the text to the clipboard.", "Working…", None),
    "READY": ("state-ready", "Text ready", "Press {key} (or the button) to copy it, then paste.",
              "Copy to clipboard", "deliver"),
    "RECOVERABLE_RESULT": ("state-idle", "Copied", "Paste it with Ctrl+V. Press {key} to dictate again.",
                           "Start dictation", "start"),
    "ERROR": ("state-error", "Needs attention", "The last recording failed. It is safe in Recovery.",
              "Open Recovery", "recovery"),
}


def shortcut_from_status(hotkey: str) -> str | None:
    match = re.match(r"registered: ([^\s—]+)", hotkey or "")
    return match.group(1) if match else None


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
        self.ai_drawer_open = False
        self.note_recording = False
        self.current_tab = "notes"
        self.settings_tab = "connections"
        self.settings_dirty = False
        self.settings: dict = {}
        self.timer: threading.Timer | None = None
        self.quick_timer: threading.Timer | None = None
        self.toast_timer: threading.Timer | None = None
        self.list_count = 0
        self.presets: list[dict] = []
        self.folders: list[dict] = []
        self.last_state = ""
        self.last_error = ""
        self.last_hotkey = None
        self.last_recovery = -1
        self.active_key = "Ctrl+Alt+D"
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
        self._render_status(self._status_or_none())
        self.thread = threading.Thread(target=self._poll, daemon=True, name="dictation-ui-poll")
        self.thread.start()

    # ── plumbing ──────────────────────────────────────────────
    def call(self, action: str, **params):
        timeout = params.pop("_timeout", 10)
        return invoke(self.paths, action, params, timeout=timeout)

    def notice(self, message: str, *, is_error: bool = False):
        """Transient toast; errors stay longer. Never contains note text or keys."""
        if self.window.closed:
            return
        self.doc.set_text("status-message", message[:280])
        self.doc.add_class("toast", "show")
        (self.doc.add_class if is_error else self.doc.remove_class)("toast", "error")
        if self.toast_timer:
            self.toast_timer.cancel()
        self.toast_timer = threading.Timer(8 if is_error else 3.5, self._hide_toast)
        self.toast_timer.daemon = True
        self.toast_timer.start()

    def _hide_toast(self):
        with self.lock:
            if not self.window.closed and not self.stop_event.is_set():
                self.doc.remove_class("toast", "show")

    def _toggle(self, ident: str, cls: str, on: bool):
        (self.doc.add_class if on else self.doc.remove_class)(ident, cls)

    def _enable(self, ident: str, enabled: bool):
        if enabled:
            self.doc.remove_attribute(ident, "disabled")
        else:
            self.doc.set_attribute(ident, "disabled", "")

    def _safe(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AppError as exc:
            self.notice(f"{exc}", is_error=True)
        except Exception:
            # UI exceptions cannot include raw provider URLs, credentials or note text.
            self._log_exception()
            self.notice("Something went wrong; details are in state/gui.log", is_error=True)
        return None

    def _log_exception(self):
        """Exception type and stack only (no locals, values or message text)."""
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
        self._toggle("settings-feedback", "error", is_error)

    # ── status polling ────────────────────────────────────────
    def _status_or_none(self):
        try:
            return self.call("status")
        except (AppError, OSError, RuntimeError):
            return None

    def _poll(self):
        while not self.stop_event.wait(.7):
            if self.window.closed:
                break
            status = self._status_or_none()
            try:
                with self.lock:
                    if self.window.closed:
                        break
                    self._render_status(status)
                    if status is None:
                        continue
                    if self.current_tab == "quick" and not self.quick_dirty:
                        self.refresh_quick()
                    if self.pending_job:
                        self._poll_job()
                    if (self.current_tab == "notes" and self.selected_note and not self.dirty
                            and time.monotonic() - self.last_note_refresh > 3):
                        self._refresh_note_if_changed()
                        self.last_note_refresh = time.monotonic()
            except (AppError, OSError, RuntimeError):
                pass
            except Exception:
                self._log_exception()

    def _render_status(self, status: dict | None):
        if status is None:
            if self.last_state != "OFFLINE":
                self.last_state = "OFFLINE"
                self.doc.set_text("connection-status", "Background service not running")
                self._set_state_classes("dictation-card", "state-offline")
                self.doc.set_text("dict-state", "Service stopped")
                self.doc.set_text("dict-hint", "Reopen Dictation4me or run “dictation4me service start”.")
                self._enable("btn-dict-action", False)
            return
        hotkey = status.get("hotkey") or ""
        if hotkey != self.last_hotkey:
            self.last_hotkey = hotkey
            self.active_key = shortcut_from_status(hotkey) or self._current_shortcut()
            self.doc.set_text("dict-key", self.active_key)
            self.doc.set_text("hotkey-keycap", shortcut_from_status(hotkey) or "Not active")
            self.doc.set_text("cfg-hotkey-status", self._describe_hotkey(hotkey))
            healthy = hotkey.startswith("registered") and "—" not in hotkey
            self._toggle("cfg-hotkey-status", "ok", healthy)
            self._toggle("cfg-hotkey-status", "bad", not healthy)
            self.last_state = ""  # hints embed the key; re-render them
        count = int(status.get("recovery_count") or 0)
        if count != self.last_recovery:
            self.last_recovery = count
            self.doc.set_text("recovery-badge", str(count))
            self._toggle("recovery-badge", "hidden", count == 0)
            if self.current_tab == "recovery":
                self.refresh_recovery()
        state, error = status["state"], status.get("error") or ""
        if state == self.last_state and error == self.last_error:
            return
        self.last_state, self.last_error = state, error
        self.doc.set_text("connection-status", "Background service running")
        css, title, hint, label, action = PRESENTATION.get(state, PRESENTATION["ERROR"])
        flow = self._flow(self.settings["quick"]) if self.settings else "review"
        if state == "RECORDING" and flow != "review":
            hint = ("Speak now. Press {key} again to stop — the text is typed into the active app."
                    if flow == "paste" else "Speak now. Press {key} again to stop and copy the text.")
            label = "Stop & paste" if flow == "paste" else "Stop & copy"
        elif state == "RECOVERABLE_RESULT" and flow == "paste":
            title, hint = "Pasted", "The text was sent to the active app. Press {key} to dictate again."
        hint = hint.format(key=self.active_key)
        if error and state in ("IDLE", "ERROR"):
            hint = error
        elif error and state == "READY":
            hint += " (last copy failed: " + error + ")"
        for ident in ("dictation-card", "quick-hero"):
            self._set_state_classes(ident, css)
        self.doc.set_text("dict-state", title)
        self.doc.set_text("dict-hint", hint)
        self.doc.set_text("dict-action-label", label)
        self.doc.set_text("quick-primary-label", label)
        self.doc.set_text("capture-title", title)
        self.doc.set_text("capture-detail", hint)
        self.doc.set_text("quick-state", state.replace("_", " ").title())
        self._toggle("quick-state", "hidden", css == "state-idle")
        for ident in ("btn-dict-action", "btn-quick-primary"):
            self._enable(ident, action is not None)
        self._toggle("btn-quick-cancel", "hidden", state not in ("RECORDING",) + BUSY)
        recording_note = state == "RECORDING" and status.get("mode") == "note"
        if recording_note != self.note_recording:
            self.note_recording = recording_note
            self._toggle("btn-stop-note", "hidden", not recording_note)
            self._toggle("btn-record-note", "hidden", recording_note)
        if state == "IDLE" and self.selected_note and self.current_tab == "notes":
            self.refresh_note_recordings()

    def _set_state_classes(self, ident: str, active: str):
        for cls in STATE_CLASSES:
            self._toggle(ident, cls, cls == active)

    @staticmethod
    def _describe_hotkey(hotkey: str) -> str:
        if hotkey.startswith("registered"):
            if "—" in hotkey:
                return ("KDE kept a different key than your preferred one. Change it in "
                        "System Settings › Keyboard › Shortcuts › Dictation4me.")
            return "Working. Press it in any app to start or stop dictation."
        if hotkey.startswith("waiting"):
            return "Waiting for you to approve the shortcut in the KDE dialog."
        if not hotkey or hotkey == "not registered":
            return "Not registered yet."
        return hotkey[0].upper() + hotkey[1:]

    def _current_shortcut(self) -> str:
        try:
            return self.settings["quick"]["shortcut"]
        except (AttributeError, KeyError, TypeError):
            return "Ctrl+Alt+D"

    def dictation_action(self):
        action = PRESENTATION.get(self.last_state, (None,) * 5)[4]
        if action == "start":
            if self.flush_quick():
                self.call("capture.start", mode="quick")
        elif action == "stop":
            self.call("capture.stop")
        elif action == "deliver":
            self.quick_deliver()
        elif action == "recovery":
            self.show_tab("recovery")

    # ── lifecycle / autosave ──────────────────────────────────
    def stop(self):
        self.stop_event.set()
        for timer in (self.timer, self.quick_timer, self.toast_timer):
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
                self.doc.set_text("save-status", "Saved")
                self.doc.remove_class("save-status", "error")
                self.doc.set_text("note-wordcount", self._words(source))
                return True
            except AppError as exc:
                self.doc.set_text("save-status", "Not saved — your text is still here")
                self.doc.add_class("save-status", "error")
                self.notice(f"Saving failed ({exc.code}). Your text is kept in the editor.", is_error=True)
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
                self.doc.set_text("quick-feedback", "Edits saved.")
                return True
            except AppError as exc:
                self.notice(f"Could not save your edit ({exc.code})", is_error=True)
                return False

    @staticmethod
    def _words(text: str) -> str:
        n = len(text.split())
        return f"{n} word" + ("" if n == 1 else "s")

    # ── events ────────────────────────────────────────────────
    def _input(self, event):
        ident = event.get("element", "")
        with self.lock:
            if ident == "note-editor":
                self.dirty = True
                self.doc.set_text("save-status", "Saving…")
                self._schedule("note")
            elif ident == "quick-editor":
                self.quick_dirty = True
                self.doc.set_text("quick-feedback", "Saving…")
                self._schedule("quick")
            elif ident == "search-input":
                self.search = self.doc.get_value("search-input")[:200]
                self.page = 0
                self._safe(self.refresh_notes)
            elif self._is_setting(ident):
                self._mark_settings_dirty()

    def _change(self, event):
        ident = event.get("element", "")
        with self.lock:
            if ident == "folder-filter":
                self.current_folder = self.doc.get_value(ident)
                self.page = 0
                self._safe(self.refresh_notes)
            elif ident in ("note-title", "note-folder", "note-tags") and self.selected_note:
                self._safe(self._save_metadata)
            elif ident == "cfg-preset-choice":
                self._safe(self._load_preset_editor)
            elif self._is_setting(ident):
                self._mark_settings_dirty()

    @staticmethod
    def _is_setting(ident: str) -> bool:
        return (ident.startswith("cfg-") and not ident.startswith("cfg-preset")
                and ident != "cfg-hotkey-status")

    def _mark_settings_dirty(self):
        if not self.settings_dirty:
            self.settings_dirty = True
            self.doc.remove_class("settings-dirty", "hidden")
            self.doc.remove_class("btn-revert-settings", "hidden")
            self.settings_feedback("")

    def _clear_settings_dirty(self):
        self.settings_dirty = False
        self.doc.add_class("settings-dirty", "hidden")
        self.doc.add_class("btn-revert-settings", "hidden")

    def _keydown(self, event):
        if not event.get("ctrl") or event.get("alt") or event.get("meta"):
            return
        key = str(event.get("key", "")).lower()
        with self.lock:
            if key == "n":
                self._safe(self.show_tab, "notes")
                self._safe(self.new_note)
            elif key == "s":
                if self.current_tab == "settings":
                    self._safe(self.save_settings)
                else:
                    self._safe(self.checkpoint)
            elif key == "k":
                self._safe(self.show_tab, "notes")
                self.doc.focus("search-input")

    def _click(self, event):
        # Nested SVG/text inside a button reports its own id or none; follow the
        # documented bubbling path to the nearest actionable id.
        path = event.get("path") or [event.get("element", "")]
        with self.lock:
            for ident in path:
                if ident.startswith(("entry--", "revision-view--", "revision-restore--",
                                     "proposal-select--", "recording-play--", "recording-append--",
                                     "recover-retry--", "recover-delete--", "recover-salvage--",
                                     "nav-", "btn-", "settab-")):
                    self._safe(self._dispatch, ident)
                    break

    def _dispatch(self, ident: str):
        prefixed = (
            ("entry--", self.load_note), ("revision-view--", self.view_revision),
            ("revision-restore--", self.restore_revision), ("proposal-select--", self.select_proposal),
            ("recording-play--", self.play_recording), ("recording-append--", self.append_recording),
            ("recover-retry--", self.retry_recording),
            ("recover-delete--", lambda ident: self.ask_delete("recording", ident)),
            ("recover-salvage--", self.salvage_recording),
            ("nav-", self.show_tab), ("settab-", self.show_settings_tab),
        )
        for prefix, handler in prefixed:
            if ident.startswith(prefix):
                return handler(ident[len(prefix):])
        actions = {
            "btn-new": self.new_note, "btn-prev": lambda: self.turn_page(-1),
            "btn-next": lambda: self.turn_page(1), "btn-folder-add": self.create_folder,
            "btn-scope-active": lambda: self.set_scope("active"),
            "btn-scope-archive": lambda: self.set_scope("archive"),
            "btn-scope-trash": lambda: self.set_scope("trash"),
            "btn-checkpoint": self.checkpoint, "btn-preview": self.toggle_preview,
            "btn-show-ai": self.toggle_ai_panel, "btn-record-note": self.record_note,
            "btn-stop-note": self.stop_recording, "btn-copy-note": self.copy_note,
            "btn-pin": lambda: self.change_flag("pinned"),
            "btn-archive": lambda: self.change_flag("archived"),
            "btn-trash": lambda: self.change_flag("trashed"),
            "btn-restore-note": lambda: self.change_flag("trashed"),
            "btn-delete-permanently": lambda: self.ask_delete("note", self.selected_note),
            "btn-delete-cancel": self.cancel_delete,
            "btn-delete-confirm": self.confirm_delete,
            "btn-transform-note": self.transform_note,
            "btn-ai-close": self.toggle_ai_panel,
            "btn-accept-current": lambda: self.accept_proposal("current"),
            "btn-accept-alternative": lambda: self.accept_proposal("alternative"),
            "btn-accept-new-note": lambda: self.accept_proposal("new-note"),
            "btn-discard-proposal": self.discard_proposal,
            "btn-dict-action": self.dictation_action, "btn-quick-primary": self.dictation_action,
            "btn-quick-cancel": self.cancel_capture,
            "btn-quick-copy": self.quick_copy, "btn-quick-promote": self.quick_promote,
            "btn-quick-transform": self.transform_quick, "btn-quick-accept-ai": self.accept_quick_ai,
            "btn-refresh-recovery": self.refresh_recovery,
            "btn-save-settings": self.save_settings, "btn-revert-settings": self.revert_settings,
            "btn-test-ai": self.test_ai, "btn-test-stt": self.test_stt,
            "btn-paste-setup": self.setup_paste,
            "btn-import-stt": lambda: self.import_key("stt"),
            "btn-import-ai": lambda: self.import_key("ai"),
            "btn-preset-new": self.new_preset, "btn-preset-save": self.save_preset,
            "btn-preset-delete": self.delete_preset,
        }
        if ident in ("btn-copy-note", "btn-checkpoint", "btn-archive", "btn-trash"):
            self._hide_popover("note-menu")
        func = actions.get(ident)
        if func:
            return func()

    def _hide_popover(self, ident: str):
        try:
            self.doc.hide_popover(ident)
        except Exception:
            pass  # already closed

    # ── navigation ────────────────────────────────────────────
    def show_tab(self, tab: str):
        if tab not in TABS:
            return
        if not self.flush() or not self.flush_quick():
            return
        for name in TABS:
            self._toggle(name + "-panel", "hidden", name != tab)
            self._toggle("nav-" + name, "active", name == tab)
        self.current_tab = tab
        if tab == "quick":
            self.refresh_quick(force=True)
        elif tab == "recovery":
            self.refresh_recovery()
        elif tab == "settings" and not self.settings_dirty:
            self.load_settings()

    def show_settings_tab(self, tab: str):
        if tab not in SETTING_TABS:
            return
        self.settings_tab = tab
        for name in SETTING_TABS:
            self._toggle("set-" + name, "hidden", name != tab)
            self._toggle("settab-" + name, "active", name == tab)

    def set_scope(self, scope: str):
        if scope not in SCOPES:
            return
        self.current_scope = scope
        self.page = 0
        for name in SCOPES:
            self._toggle("btn-scope-" + name, "active", name == scope)
        self.refresh_notes()

    # ── notes ─────────────────────────────────────────────────
    def refresh_notes(self):
        items = self.call("notes.list", search=self.search, scope=self.current_scope,
                          folder_id=self.current_folder or None, limit=41, offset=self.page * 40)
        self.list_count = len(items)
        visible = items[:40]
        result = []
        for n in visible:
            active = " selected" if n["id"] == self.selected_note else ""
            pin = '<span class="pin">★</span>' if n["pinned"] else ""
            plain = self._plain(n["excerpt"] or "")
            excerpt = esc(plain) if plain else '<span class="muted">Empty note</span>'
            result.append(f'<button id="entry--{esc(n["id"])}" class="note-entry{active}" role="listitem">'
                          f'<div class="entry-title">{pin}{esc(n["title"] or "Untitled")}</div>'
                          f'<div class="entry-excerpt">{excerpt}</div>'
                          f'<div class="entry-meta">{esc(self._when(n["updated_at"]))}</div></button>')
        if not result:
            empty = {"active": "No notes here yet. Press + to write one.",
                     "archive": "Nothing archived.", "trash": "Trash is empty."}[self.current_scope]
            if self.search:
                empty = "No notes match your search."
            result.append(f'<div class="empty-message">{esc(empty)}</div>')
        self.doc.replace_html("note-items", "".join(result))
        self.doc.set_text("page-number", f"Page {self.page + 1}")
        self._toggle("pager", "hidden", self.page == 0 and self.list_count <= 40)
        label = {"active": "notes", "archive": "archived", "trash": "in Trash"}[self.current_scope]
        self.doc.set_text("footer-count", f"{len(visible)} {label}")

    @staticmethod
    def _plain(markdown: str) -> str:
        """List excerpts read as prose: drop common Markdown markers."""
        text = re.sub(r"(?m)^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+|\d+\.\s+)", "", markdown)
        text = re.sub(r"[*_`~]{1,3}|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _when(stamp: str) -> str:
        return (stamp or "")[:16].replace("T", " ")

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
        self._hide_popover("folder-popover")
        self.refresh_folders()
        if self.selected_note:
            note = self.call("notes.get", id=self.selected_note)
            self.doc.set_value("note-folder", note["folder_id"] or "")
        self.notice(f"Folder “{name}” created")

    def new_note(self):
        if not self.flush():
            return
        note = self.call("notes.create", title="Untitled")
        if self.current_scope != "active":
            self.set_scope("active")
        self.load_note(note["id"])
        self.doc.focus("note-title")

    def load_note(self, note_id: str):
        if not self.flush():
            return
        n = self.call("notes.get", id=note_id)
        self.selected_note = n["id"]
        self.note_seq = n["edit_seq"]
        self.dirty = False
        self.doc.set_value("note-title", "" if n["title"] == "Untitled" else n["title"])
        self.doc.set_value("note-editor", n["content_md"])
        self.doc.set_value("note-tags", ", ".join(n["tags"]))
        self.doc.set_value("note-folder", n["folder_id"] or "")
        self._render_crumb(n)
        self.doc.set_text("note-wordcount", self._words(n["content_md"]))
        self.doc.set_text("save-status", "Saved")
        self.doc.remove_class("save-status", "error")
        self.doc.set_text("pin-label", "Unpin" if n["pinned"] else "Pin")
        self._toggle("btn-pin", "pinned", bool(n["pinned"]))
        self.doc.set_text("btn-archive", "Unarchive" if n["archived_at"] else "Archive")
        self._toggle("trash-banner", "hidden", not n["trashed_at"])
        self._toggle("btn-trash", "hidden", bool(n["trashed_at"]))
        if self.preview_on:
            self.doc.replace_html("note-preview", render(n["content_md"]))
        self.refresh_notes()
        self.refresh_revisions()
        self.refresh_note_recordings()
        self.doc.set_value("proposal-text", "")
        self.doc.set_text("proposal-meta", "No proposal yet.")
        self.selected_proposal = None

    def _render_crumb(self, note: dict):
        folder = next((f["name"] for f in self.folders if f["id"] == note["folder_id"]), None)
        where = {"archived_at": "Archive", "trashed_at": "Trash"}
        place = next((label for key, label in where.items() if note.get(key)), "Notes")
        self.doc.set_text("current-note-path", " › ".join(x for x in (place, folder) if x))

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
        title = self.doc.get_value("note-title")[:500].strip() or "Untitled"
        folder_id = self.doc.get_value("note-folder") or None
        tags = [x.strip() for x in self.doc.get_value("note-tags").split(",") if x.strip()]
        self.call("notes.fields", id=self.selected_note,
                  fields={"title": title, "folder_id": folder_id, "tags": tags})
        self._render_crumb(self.call("notes.get", id=self.selected_note))
        self.refresh_notes()

    def change_flag(self, flag: str):
        if not self.selected_note or not self.flush():
            return
        n = self.call("notes.get", id=self.selected_note)
        value = bool(n["pinned"]) if flag == "pinned" else bool(n[flag + "_at"])
        self.call("notes.fields", id=self.selected_note, fields={flag: not value})
        self.reload_current()
        messages = {("pinned", False): "Pinned", ("pinned", True): "Unpinned",
                    ("archived", False): "Moved to Archive", ("archived", True): "Restored from Archive",
                    ("trashed", False): "Moved to Trash — restore it from the Trash list",
                    ("trashed", True): "Restored from Trash"}
        self.notice(messages[(flag, value)])

    def ask_delete(self, kind: str, identifier: str | None):
        if not identifier or not self.flush() or not self.flush_quick():
            return
        if kind == "note" and not self.call("notes.get", id=identifier)["trashed_at"]:
            return self.notice("Move the note to Trash before deleting it permanently", is_error=True)
        self.pending_deletion = (kind, identifier)
        self.doc.set_text("delete-title", "Delete this note forever?" if kind == "note"
                          else "Discard this recording?")
        self.doc.set_text("delete-description",
                          "The note, all its versions and attached recordings are erased. This cannot be undone."
                          if kind == "note" else
                          "The audio is deleted and cannot be transcribed again. This cannot be undone.")
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
            self.notice("Recording discarded")
            return
        self.call("notes.purge", id=identifier)
        self.selected_note = None
        self.note_seq = 0
        self.dirty = False
        first = self.call("notes.list", scope=self.current_scope, limit=1)
        if not first:
            self.set_scope("active")
            first = self.call("notes.list", scope="active", limit=1)
        if first:
            self.load_note(first[0]["id"])
        else:
            self.new_note()
        self.notice("Note deleted permanently")

    def checkpoint(self):
        if self.selected_note and self.flush():
            result = self.call("notes.checkpoint", id=self.selected_note)
            self.refresh_revisions()
            self.notice("Version saved" if result["created"] else "No changes since the last version")

    def toggle_preview(self):
        if self.preview_on:
            self.doc.add_class("note-preview", "hidden")
            self.doc.remove_class("note-editor", "hidden")
            self.doc.set_text("preview-label", "Preview")
        else:
            body = self.doc.get_value("note-editor")
            self.doc.replace_html("note-preview", render(body))
            self.doc.remove_class("note-preview", "hidden")
            self.doc.add_class("note-editor", "hidden")
            self.doc.set_text("preview-label", "Edit")
        self.preview_on = not self.preview_on
        self._toggle("btn-preview", "active", self.preview_on)

    def toggle_ai_panel(self):
        self.ai_drawer_open = not self.ai_drawer_open
        self._toggle("ai-panel", "drawer-open", self.ai_drawer_open)
        self._toggle("btn-show-ai", "active", self.ai_drawer_open)

    def copy_note(self):
        if self.selected_note:
            self.app.set_clipboard_text(self.doc.get_value("note-editor"))
            self.notice("Note text copied")

    def refresh_revisions(self):
        if not self.selected_note:
            return
        labels = {"initial": "Created", "checkpoint": "Saved version", "transcript": "Dictation added",
                  "restore": "Restored", "ai": "AI accepted"}
        result = []
        for revision in self.call("notes.revisions", id=self.selected_note):
            ident = esc(revision["id"])
            label = labels.get(revision["kind"], revision["kind"].title())
            result.append(f'<div class="compact-row"><span>{esc(label)} · {esc(self._when(revision["created_at"]))}'
                          f'</span><button id="revision-view--{ident}" class="ghost">View</button>'
                          f'<button id="revision-restore--{ident}">Restore</button></div>')
        self.doc.replace_html("revisions-list", "".join(result) or '<div class="empty-row">No versions yet</div>')
        proposals = self.call("ai.proposals", note_id=self.selected_note)
        self.doc.replace_html("proposals-list", "".join(
            f'<div class="compact-row"><span>{esc(self._preset_name(p["preset_id"]))} · {esc(p["status"])}'
            f'</span><button id="proposal-select--{esc(p["id"])}" class="ghost">View</button></div>'
            for p in proposals) or '<div class="empty-row">No AI rewrites yet</div>')

    def _preset_name(self, preset_id: str) -> str:
        return next((p["name"] for p in self.presets if p["id"] == preset_id), preset_id)

    def view_revision(self, revision_id: str):
        rev = self.call("notes.revision", id=revision_id)
        if rev["note_id"] != self.selected_note:
            return
        self.selected_proposal = None
        self.doc.set_value("proposal-text", rev["content_md"])
        self.doc.set_text("proposal-meta", "Viewing an older version. Use Restore in Versions to bring it back.")

    def restore_revision(self, revision_id: str):
        if self.selected_note and self.flush():
            self.call("notes.restore", note_id=self.selected_note, revision_id=revision_id,
                      base_edit_seq=self.note_seq)
            self.reload_current()
            self.notice("Older version restored (saved as a new version)")

    def refresh_note_recordings(self):
        if not self.selected_note:
            return
        rows = self.call("recordings.note", note_id=self.selected_note)
        result = []
        for r in rows:
            ident = esc(r["id"])
            details = f'{esc(self._when(r["created_at"]))} · {int(r["duration_ms"] or 0) // 1000}s · {esc(r["status"])}'
            buttons = (f'<button id="recording-play--{ident}" class="ghost">Play</button>' if r["file_relpath"] else "")
            if r["original_text"]:
                buttons += f'<button id="recording-append--{ident}">Add text to note</button>'
            result.append(f'<div class="compact-row"><span>{details}</span>{buttons}</div>')
        self.doc.set_text("recordings-count", str(len(rows)))
        self.doc.replace_html("note-recordings", "".join(result) or
                              '<div class="empty-row">No recordings. Press Record to dictate into this note.</div>')

    def play_recording(self, recording_id: str):
        self.call("recordings.play", id=recording_id)
        self.notice("Playing recording…")

    def append_recording(self, recording_id: str):
        if self.flush():
            self.call("recordings.append", id=recording_id)
            self.reload_current()

    # ── AI on notes ───────────────────────────────────────────
    def refresh_presets(self):
        self.presets = self.call("presets.list")
        options = "".join(option(p["id"], p["name"]) for p in self.presets)
        self.doc.replace_html("preset-select", option("", "Choose an instruction…") + options)
        self.doc.replace_html("quick-preset", option("", "Choose an instruction…") + options)
        self.doc.replace_html("cfg-preset-choice", option("", "New preset…") + options)
        self.doc.replace_html("cfg-auto-preset", option("", "Off (recommended)") + options)

    def transform_note(self):
        if not self.selected_note or not self.flush():
            return
        preset_id = self.doc.get_value("preset-select")
        if not preset_id:
            return self.notice("Choose an instruction first")
        result = self.call("ai.note", note_id=self.selected_note, preset_id=preset_id)
        self.pending_job = {"id": result["job_id"], "kind": "note"}
        self.doc.set_text("proposal-meta", "Working on it… you can keep editing.")
        self.notice("AI rewrite started")

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
            code = result["job"].get("error_code") or "unknown error"
            self.doc.set_text("proposal-meta", f"The AI rewrite failed ({code}).")
            self.notice(f"AI rewrite failed ({code}). Check Settings › Connections.", is_error=True)
            return
        p = result["proposal"]
        if task["kind"] == "note":
            self.selected_proposal = p["id"]
            self.doc.set_value("proposal-text", p["proposed_content_md"])
            self.doc.set_text("proposal-meta", f"Proposal from “{self._preset_name(p['preset_id'])}”. "
                                               "Review it, then choose what to do.")
            self.refresh_revisions()
            if not self.ai_drawer_open:
                self.toggle_ai_panel()
        else:
            self.quick_proposal = p["id"]
            self.doc.set_value("quick-ai-text", p["proposed_content_md"])
            self.notice("AI proposal ready")

    def select_proposal(self, proposal_id: str):
        p = self.call("ai.proposal", id=proposal_id)
        self.selected_proposal = p["id"] if p["status"] == "pending" else None
        self.doc.set_value("proposal-text", p["proposed_content_md"])
        self.doc.set_text("proposal-meta", f"“{self._preset_name(p['preset_id'])}” · {p['status']}")

    def accept_proposal(self, action: str):
        if not self.selected_proposal or not self.flush():
            return self.notice("There is no pending proposal to apply")
        result = self.call("ai.accept", id=self.selected_proposal, action=action)
        self.selected_proposal = None
        if action == "current" and result["note_id"] == self.selected_note:
            self.reload_current()
        elif action == "new-note":
            self.load_note(result["note_id"])
        self.refresh_revisions()
        self.notice({"current": "Note replaced with the proposal (previous text kept as a version)",
                     "alternative": "Proposal kept as a version",
                     "new-note": "Proposal saved as a new note"}[action])

    def discard_proposal(self):
        if self.selected_proposal:
            self.call("ai.discard", id=self.selected_proposal)
            self.selected_proposal = None
            self.doc.set_value("proposal-text", "")
            self.doc.set_text("proposal-meta", "Proposal discarded.")
            self.refresh_revisions()

    def record_note(self):
        if self.selected_note and self.flush():
            self.call("capture.start", mode="note", note_id=self.selected_note)
            self.notice("Recording into this note — press Stop when done")

    def stop_recording(self):
        self.call("capture.stop")
        self.notice("Transcribing… the text is added to the note when ready")
        self.refresh_note_recordings()

    def cancel_capture(self):
        self.call("capture.cancel")
        self.notice("Cancelled. Any audio is kept in Recovery.")

    # ── quick dictation ───────────────────────────────────────
    def refresh_quick(self, *, force=False):
        q = self.call("quick.get")
        for ident in ("btn-quick-copy", "btn-quick-promote", "btn-quick-transform"):
            self._enable(ident, bool(q and q.get("edited_text")))
        if not q:
            if force:
                self.doc.set_value("quick-editor", "")
            return
        if (force or self.quick_id != q["id"] or self.quick_text_last != q["edited_text"]) and not self.quick_dirty:
            self.quick_id = q["id"]
            self.quick_text_last = q["edited_text"]
            self.doc.set_value("quick-editor", q["edited_text"] or "")

    def quick_copy(self):
        if self.flush_quick():
            self.call("quick.copy")
            self.notice("Copied to the clipboard")

    def quick_deliver(self):
        if self.flush_quick():
            result = self.call("quick.deliver")
            sent = result["paste_status"] == "sent"
            self.notice("Copied — paste keys sent" if sent else "Copied to the clipboard — paste with Ctrl+V")

    def quick_promote(self):
        if self.flush_quick() and self.quick_id:
            n = self.call("quick.save-note", id=self.quick_id)
            self.show_tab("notes")
            self.load_note(n["id"])
            self.notice("Saved as a note")

    def transform_quick(self):
        if not self.flush_quick() or not self.quick_id:
            return self.notice("There is no dictation to rewrite yet")
        preset = self.doc.get_value("quick-preset")
        if not preset:
            return self.notice("Choose an instruction first")
        result = self.call("ai.quick", quick_id=self.quick_id, preset_id=preset)
        self.pending_job = {"id": result["job_id"], "kind": "quick"}
        self.notice("AI rewrite started")

    def accept_quick_ai(self):
        if not self.quick_id or not self.quick_proposal or not self.flush_quick():
            return
        self.call("ai.accept-quick", quick_id=self.quick_id, id=self.quick_proposal)
        self.quick_proposal = None
        self.refresh_quick(force=True)
        self.notice("Rewritten text applied; the original transcript is kept")

    # ── recovery ──────────────────────────────────────────────
    def refresh_recovery(self):
        items = self.call("recordings.recovery")
        reasons = {"NO_SPEECH": "No speech detected", "PROVIDER_HTTP": "Server returned an error",
                   "PROVIDER_UNAVAILABLE": "Server unreachable", "SECRET": "API key unavailable",
                   "MODEL_NOT_CONFIGURED": "No model configured", "CONSENT": "Remote server not approved",
                   "AUDIO_EMPTY": "Recording was empty", "AUDIO_MISSING": "Audio file missing"}
        result = []
        for r in items:
            ident = esc(r["id"])
            code = r["error_code"] or ""
            reason = reasons.get(code, code.replace("_", " ").capitalize() or "Not transcribed yet")
            kind = "Quick dictation" if r["mode"] == "quick" else "Voice note"
            size = f'{(r["size_bytes"] or 0) / 1024:.0f} KB'
            action = (f'<button id="recover-retry--{ident}" class="primary small">Try again</button>' if r["file_relpath"]
                      else f'<button id="recover-salvage--{ident}" class="small">Repair audio</button>')
            result.append(f'<div class="recovery-card"><div class="info"><div class="title">{kind}</div>'
                          f'<div class="sub">{esc(self._when(r["created_at"]))} · {size}</div>'
                          f'<span class="reason">{esc(reason)}</span></div>{action}'
                          f'<button id="recover-delete--{ident}" class="ghost danger small">Discard</button></div>')
        if not result:
            result.append('<div class="empty-state"><svg fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">'
                          '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.7 2.7L16.5 9.5"/></svg>'
                          '<strong>Nothing to recover</strong><p>If a recording ever fails to transcribe, '
                          'it is kept here so you can try again later.</p></div>')
        self.doc.replace_html("recovery-items", "".join(result))

    def retry_recording(self, recording_id: str):
        self.call("recordings.retry", id=recording_id)
        self.notice("Trying again…")
        self.refresh_recovery()

    def salvage_recording(self, recording_id: str):
        self.call("recordings.salvage", id=recording_id)
        self.notice("Audio repaired — you can try transcribing it again")
        self.refresh_recovery()

    # ── settings ──────────────────────────────────────────────
    def load_settings(self):
        self.settings = self.call("config.get")
        for prefix in ("stt", "ai"):
            cfg = self.settings[prefix]
            for short, key in (("url", "base_url"), ("path", "path"), ("model", "model"),
                               ("source", "key_source"), ("ref", "key_reference")):
                self.doc.set_value(f"cfg-{prefix}-{short}", cfg[key])
            self.doc.set_value(f"cfg-{prefix}-secret", "")
        self.doc.set_value("cfg-stt-profile", self.settings["stt"]["request_profile"])
        self.doc.set_value("cfg-shortcut", self.settings["quick"]["shortcut"])
        self.doc.set_value("cfg-flow", self._flow(self.settings["quick"]))
        self.doc.set_value("cfg-paste-shortcut", self.settings["quick"]["paste_shortcut"])
        self.doc.set_value("cfg-auto-preset", self.settings["ai"]["automatic_quick_preset_id"])
        self.doc.set_value("cfg-retention", str(self.settings["quick"]["quick_text_retention_hours"]))
        self.doc.set_checked("cfg-unsafe-wayland", self.settings["quick"]["paste_to_current_focus_unsafe_wayland"])
        self.doc.set_checked("cfg-retain-quick-audio", self.settings["quick"]["record_audio_success_retention"] == "keep")
        self.doc.set_checked("cfg-remote-approved", self.settings["privacy"]["remote_provider_approved"])
        try:
            devices = self.call("audio.devices")
        except AppError:
            devices = []
        self.doc.replace_html("cfg-audio-device", option("default", "System default") + "".join(
            option(str(d["id"]), d["name"]) for d in devices if d["name"] != "default"))
        self.doc.set_value("cfg-audio-device", str(self.settings["audio"]["device"]))
        self.refresh_credentials()
        self.refresh_paste_status()
        self.render_about()
        self._clear_settings_dirty()

    def refresh_credentials(self):
        try:
            status = self.call("credentials.status")
        except AppError:
            return
        for section in ("stt", "ai"):
            info = status[section]
            if info["source"] == "none":
                text, ok = "No key is sent to this server.", True
            elif info["available"]:
                text, ok = "Key found: " + info["detail"].lower() + ".", True
            else:
                text, ok = "Key not available to the background service: " + info["detail"], False
            if info["available"] and info["source"] == "environment":
                text += " It will be missing if the service starts at login — move it to the keyring."
                ok = False
            self.doc.set_text(f"cred-{section}-status", text)
            self._toggle(f"cred-{section}-status", "ok", ok)
            self._toggle(f"cred-{section}-status", "bad", not ok)
            self._toggle(f"btn-import-{section}", "hidden", not info["can_import"])

    @staticmethod
    def _flow(quick: dict) -> str:
        if not quick["auto_deliver"]:
            return "review"
        return "paste" if quick["output_mode"] == "attempt-paste" else "copy"

    @staticmethod
    def _flow_settings(flow: str) -> dict:
        return {"paste": {"auto_deliver": True, "output_mode": "attempt-paste"},
                "copy": {"auto_deliver": True, "output_mode": "clipboard"}}.get(
                    flow, {"auto_deliver": False, "output_mode": "clipboard"})

    def refresh_paste_status(self):
        pasting = self.settings["quick"]["output_mode"] == "attempt-paste"
        self._toggle("paste-setup", "hidden", not pasting)
        if not pasting:
            return
        try:
            info = self.call("paste.status")
        except AppError:
            return
        if not info["needs_permission"]:
            text, ok = "Pasting works without extra permission on this desktop.", True
        elif info["authorized"]:
            text, ok = "Keyboard access allowed — dictation is pasted automatically.", True
        else:
            text, ok = ("Not allowed yet. Click the button once and approve the KDE dialog, "
                        "so it never interrupts a dictation."), False
        if info.get("last_error") and not ok:
            text += " Last attempt: " + info["last_error"]
        self.doc.set_text("paste-status", text)
        self._toggle("paste-status", "ok", ok)
        self._toggle("paste-status", "bad", not ok)
        self._toggle("btn-paste-setup", "hidden", ok)

    def setup_paste(self):
        self.doc.set_text("paste-status", "Waiting for you to approve the KDE dialog…")

        def work():
            try:
                self.call("paste.setup", _timeout=150)
                if not self.stop_event.is_set():
                    with self.lock:
                        self.refresh_paste_status()
                        self.settings_feedback("Automatic pasting is allowed")
            except AppError as exc:
                if not self.stop_event.is_set():
                    self.settings_feedback(f"Keyboard access not granted: {exc}", is_error=True)
                    with self.lock:
                        self.refresh_paste_status()
        threading.Thread(target=work, name="dictation-paste-setup", daemon=True).start()

    def render_about(self):
        status = self._status_or_none() or {}
        rows = (("Version", status.get("version", "—")), ("Desktop", status.get("session", "—")),
                ("Notes database", str(self.paths.database)), ("Settings file", str(self.paths.config_file)))
        self.doc.replace_html("about-info", "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows))

    def revert_settings(self):
        self.load_settings()
        self.settings_feedback("Changes discarded")

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
            **self._flow_settings(self.doc.get_value("cfg-flow")),
            "paste_shortcut": self.doc.get_value("cfg-paste-shortcut"),
            "quick_text_retention_hours": int(self.doc.get_value("cfg-retention") or 24),
            "paste_to_current_focus_unsafe_wayland": bool(self.doc.get_checked("cfg-unsafe-wayland"))}
        try:
            result = self.call("config.save", changes=changes, secrets=secrets)
        except AppError as exc:
            # Nothing was written: the service validates the whole change set first.
            self.settings_feedback(f"Not saved — {exc}", is_error=True)
            raise
        self.settings = result["settings"]
        self.load_settings()
        message = "Settings saved"
        if result.get("shortcut_changed"):
            message += " — shortcut updated"
        self.last_hotkey = None  # re-render shortcut status and state hints on next poll
        self.settings_feedback(message + time.strftime(" at %H:%M"))
        self.notice(message)

    def import_key(self, section: str):
        result = self.call("credentials.import-env", provider=section)
        self.settings = result["settings"]
        self.load_settings()
        self.settings_feedback("Key moved to the system keyring; it now works at login too")

    def _load_preset_editor(self):
        ident = self.doc.get_value("cfg-preset-choice")
        preset = next((p for p in self.presets if p["id"] == ident), None)
        self.doc.set_value("cfg-preset-name", preset["name"] if preset else "")
        self.doc.set_value("cfg-preset-prompt", preset["instruction"] if preset else "")

    def new_preset(self):
        self.doc.set_value("cfg-preset-choice", "")
        self.doc.set_value("cfg-preset-name", "")
        self.doc.set_value("cfg-preset-prompt", "")
        self.doc.focus("cfg-preset-name")

    def save_preset(self):
        name = self.doc.get_value("cfg-preset-name").strip()
        instr = self.doc.get_value("cfg-preset-prompt").strip()
        if not name or not instr:
            return self.notice("A preset needs a name and an instruction", is_error=True)
        ident = self.doc.get_value("cfg-preset-choice")
        if not ident:
            ident = re.sub(r"[^a-z0-9-]", "-", name.lower().replace(" ", "-"))[:70].strip("-") + "-" + uuid.uuid4().hex[:5]
        self.call("presets.save", preset={"id": ident, "name": name, "description": "Custom preset",
                                          "instruction": instr, "preserve_language": True,
                                          "output_format": "markdown"})
        self.refresh_presets()
        self.doc.set_value("cfg-preset-choice", ident)
        self.notice(f"Preset “{name}” saved")

    def delete_preset(self):
        ident = self.doc.get_value("cfg-preset-choice")
        if ident:
            self.call("presets.delete", id=ident)
            self.refresh_presets()
            self.new_preset()
            self.notice("Preset deleted")

    def test_ai(self):
        self.save_settings()
        self.settings_feedback("Testing the AI server…")

        def work():
            try:
                result = self.call("provider.test-ai", _timeout=130)
                if not self.stop_event.is_set():
                    self.settings_feedback(f"AI server works — the model replied ({result.get('characters')} characters)")
            except AppError as exc:
                if not self.stop_event.is_set():
                    self.settings_feedback(f"AI test failed: {exc}", is_error=True)
        threading.Thread(target=work, name="dictation-ai-test", daemon=True).start()

    def test_stt(self):
        self.save_settings()
        # Explicit native picker; no file/recording is sent without this action.
        future = self.window.open_files(multiple=False, title="Choose a short WAV recording to test",
                                        filters=[{"name": "WAV audio", "extensions": ["wav"]}], wait=False)

        def selected(fut):
            try:
                value = fut.result()
                if not value["cancelled"] and value["paths"]:
                    self.settings_feedback("Testing speech-to-text…")
                    result = self.call("provider.test-stt", file=value["paths"][0], _timeout=130)
                    self.settings_feedback(f"Speech-to-text works — {result.get('characters')} characters transcribed")
            except AppError as exc:
                self.settings_feedback(f"Speech-to-text test failed: {exc}", is_error=True)
            except Exception:
                self.settings_feedback("The file dialog could not be opened", is_error=True)
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
        window = app.open_file(asset, title="Dictation4me", width=1280, height=820,
                               min_width=700, min_height=480)
        _ui = NotebookUI(app, window, paths, initial_tab=initial_tab)  # kept alive by app.run()
        app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
