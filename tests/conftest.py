import pytest

from dictation4me.paths import Paths


@pytest.fixture
def app_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("DICTATION4ME_HOME", str(tmp_path / "app"))
    return Paths.discover().init()


@pytest.fixture(autouse=True)
def _no_real_desktop_bindings(monkeypatch):
    # A service subprocess spawned by a test inherits the developer's graphical
    # session; never let it register or rebind the real global shortcut.
    monkeypatch.setenv("DICTATION4ME_NO_HOTKEY", "1")
    monkeypatch.setenv("DICTATION4ME_NO_TRAY", "1")
