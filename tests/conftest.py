from __future__ import annotations
import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DICTATION4ME_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    return tmp_path / "home"
