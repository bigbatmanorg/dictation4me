"""Headless SDK-contract harness for notebook UI (not a native desktop acceptance test)."""
from __future__ import annotations

from html.parser import HTMLParser

import pytest

from dictation4me.engine import Engine
from dictation4me.gui import NotebookUI


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = set()

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        if found.get('id'):
            self.elements.add(found['id'])


class FakeDocument:
    def __init__(self):
        from importlib.resources import files
        self.source = files('dictation4me').joinpath('assets/notebook.html').read_text('utf-8')
        parser = Elements()
        parser.feed(self.source)
        self.elements = parser.elements
        self.values = {}
        self.texts = {}
        self.markup = {}
        self.checks = {}
        self.classes = {}
        self.events = {}
        self.shortcut_rules = []
        self.last_focus = None

    def on(self, event, callback, element=None):
        self.events[event] = callback

    def prevent_default(self, element, event, **kwargs):
        self._assert(element)
        self.shortcut_rules.append((element, event, kwargs))

    def focus(self, element):
        self._assert(element)
        self.last_focus = element

    def _assert(self, ident):
        assert ident in self.elements, f'Invalid or stale element id: {ident}'

    def get_value(self, ident):
        self._assert(ident)
        return self.values.get(ident, '')

    def set_value(self, ident, value):
        self._assert(ident)
        self.values[ident] = value

    def get_text(self, ident):
        self._assert(ident)
        return self.texts.get(ident, '')

    def set_text(self, ident, text):
        self._assert(ident)
        self.texts[ident] = text

    def replace_html(self, ident, markup):
        self._assert(ident)
        self.markup[ident] = markup

    def add_class(self, ident, name):
        self._assert(ident)
        self.classes.setdefault(ident, set()).add(name)

    def remove_class(self, ident, name):
        self._assert(ident)
        self.classes.setdefault(ident, set()).discard(name)

    def set_checked(self, ident, value):
        self._assert(ident)
        self.checks[ident] = bool(value)

    def get_checked(self, ident):
        self._assert(ident)
        return self.checks.get(ident, False)

    def show_dialog(self, ident):
        self._assert(ident)
        self.classes.setdefault(ident, set()).add('open')

    def close_dialog(self, ident):
        self._assert(ident)
        self.classes.setdefault(ident, set()).discard('open')


class FakeWindow:
    def __init__(self):
        self.closed = False
        self.document = FakeDocument()
        self.before_close = None

    def on_before_close(self, callback):
        self.before_close = callback


class FakeApp:
    def __init__(self):
        self.copy = None
        self.shutdown = None

    def on_shutdown(self, callback):
        self.shutdown = callback

    def set_clipboard_text(self, value):
        self.copy = value


@pytest.fixture
def ui_harness(monkeypatch, app_paths):
    import dictation4me.desktop
    monkeypatch.setattr(dictation4me.desktop, 'notify', lambda _: None)
    engine = Engine(app_paths)
    monkeypatch.setattr(NotebookUI, 'call', lambda self, action, **params: engine.handle(action, params))
    app = FakeApp()
    window = FakeWindow()
    ui = NotebookUI(app, window, app_paths)
    try:
        yield engine, ui, app, window
    finally:
        ui.stop()
        window.closed = True
        engine.close()


def test_initial_notebook_is_writable_and_autosaves(ui_harness):
    engine, ui, app, window = ui_harness
    assert ui.selected_note
    assert engine.store.integrity()['notes'] == 1
    ui.doc.set_value('note-editor', 'My first saved idea — **important**')
    ui._input({'element': 'note-editor'})
    assert ui.flush()
    note = engine.store.get_note(ui.selected_note)
    assert note['content_md'] == 'My first saved idea — **important**'
    assert ui.note_seq == 1
    assert window.before_close() is True
    ui.toggle_preview()
    assert 'hidden' in ui.doc.classes['note-editor']


def test_metadata_navigation_and_nested_click(ui_harness):
    engine, ui, _, _ = ui_harness
    original_id = ui.selected_note
    ui.new_note()
    another_id = ui.selected_note
    assert original_id != another_id
    ui.doc.set_value('note-title', 'Design & research')
    ui.doc.set_value('note-tags', 'architecture, local-first')
    ui._change({'element': 'note-title'})
    assert engine.store.get_note(another_id)['title'] == 'Design & research'
    ui._click({'element': '', 'path': ['entry--' + original_id, 'note-items']})
    assert ui.selected_note == original_id
    assert 'Design &amp; research' in ui.doc.markup['note-items']


def test_configure_provider_through_ui_without_secret(ui_harness):
    engine, ui, app, window = ui_harness
    ui.show_tab('settings')
    ui.doc.set_value('cfg-stt-model', 'test-speech-model')
    ui.doc.set_value('cfg-ai-model', 'test-writing-model')
    ui.doc.set_value('cfg-ai-url', 'http://localhost:4000/v1')
    ui.save_settings()
    assert engine.settings['stt']['model'] == 'test-speech-model'
    assert engine.settings['ai']['model'] == 'test-writing-model'
    assert 'test-writing-model' in ui.paths.config_file.read_text('utf-8')
    assert 'Settings saved' in ui.doc.texts['status-message']


def test_ui_html_contract_ids_and_no_scripts():
    from importlib.resources import files
    source = files('dictation4me').joinpath('assets/notebook.html').read_text('utf-8')
    parser = Elements()
    parser.feed(source)
    assert '<script' not in source.lower()
    assert 'contenteditable' not in source.lower()
    assert len(parser.elements) > 50
    assert 'note-editor' in parser.elements
    assert 'quick-editor' in parser.elements
    assert 'cfg-ai-model' in parser.elements


def test_delete_note_requires_trash_and_dialog(ui_harness):
    engine, ui, _, _ = ui_harness
    ident = ui.selected_note
    ui.ask_delete('note', ident)
    assert ui.pending_deletion is None
    assert engine.store.get_note(ident)
    ui.change_flag('trashed')
    ui.ask_delete('note', ident)
    assert ui.pending_deletion == ('note', ident)
    assert 'open' in ui.doc.classes['confirm-deletion']
    ui.cancel_delete()
    assert engine.store.get_note(ident)
    ui.ask_delete('note', ident)
    ui.confirm_delete()
    assert ui.selected_note != ident
    assert engine.store.integrity()['foreign_keys'] == 0


def test_keyboard_shortcuts_and_small_ai_drawer(ui_harness):
    engine, ui, _, _ = ui_harness
    previous = ui.selected_note
    ui._keydown({'ctrl': True, 'key': 'n'})
    assert ui.selected_note != previous
    ui.doc.set_value('note-editor', 'Edited from shortcut')
    ui._input({'element': 'note-editor'})
    ui._keydown({'ctrl': True, 'key': 's'})
    assert engine.store.get_note(ui.selected_note)['content_md'] == 'Edited from shortcut'
    ui._keydown({'ctrl': True, 'key': 'k'})
    assert ui.doc.last_focus == 'search-input'
    ui.toggle_ai_panel()
    assert 'drawer-open' in ui.doc.classes['ai-panel']
    ui._click({'element': 'btn-ai-close'})
    assert 'drawer-open' not in ui.doc.classes['ai-panel']
