"""Regressions found on a real KDE Wayland workstation (2026-11-17)."""
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from dictation4me import cli, config, onboarding
from dictation4me.errors import AppError


def fake_dns(mapping):
    def getaddrinfo(host, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]]
    return getaddrinfo


@pytest.mark.parametrize("url", ["http://pgx:4000/v1", "http://litellm.lan/v1",
                                  "http://box.home/v1", "http://ai.internal:4000/v1",
                                  "http://pgx.home/v1", "http://10.0.90.10:4000/v1"])
def test_lan_http_endpoints_save(app_paths, monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({}))
    saved = config.merge(app_paths, {"ai": {"base_url": url, "model": "brain-direct",
                                            "key_source": "environment",
                                            "key_reference": "LITELLM_API_KEY"}})
    assert saved["ai"]["base_url"] == url
    assert not config.is_remote_provider(url)


def test_dns_name_resolving_to_private_address_is_local(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns({"gpu.example.org": ["10.1.2.3"],
                                                         "api.example.com": ["93.184.216.34"]}))
    assert config.validate({"stt": {"base_url": "http://gpu.example.org/v1"}})
    assert not config.is_remote_provider("https://gpu.example.org/v1")
    assert config.is_remote_provider("https://api.example.com/v1")
    with pytest.raises(AppError):
        config.validate({"stt": {"base_url": "http://api.example.com/v1"}})


@pytest.mark.parametrize("raw,canonical", [("Meta+L", "Meta+L"), ("super+d", "Meta+D"),
                                           ("Win+Shift+F9", "Shift+Meta+F9"),
                                           ("alt+ctrl+d", "Ctrl+Alt+D"),
                                           ("Control + Alt + 5", "Ctrl+Alt+5")])
def test_shortcut_aliases_normalize(raw, canonical):
    assert config.validate({"quick": {"shortcut": raw}})["quick"]["shortcut"] == canonical


@pytest.mark.parametrize("raw", ["L", "Meta+", "Hyper+L", "Ctrl+Ctrl+D", "Shift+D", "Ctrl+Space"])
def test_bad_shortcuts_rejected(raw):
    with pytest.raises(AppError):
        config.validate({"quick": {"shortcut": raw}})


def test_portal_trigger_maps_meta_to_logo():
    # Mirrors PortalShortcut trigger construction for the canonical form.
    mapping = {"ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT", "super": "LOGO", "meta": "LOGO"}
    parts = "Ctrl+Meta+D".lower().split("+")
    assert "+".join([mapping[m] for m in parts[:-1]] + [parts[-1].upper()]) == "CTRL+LOGO+D"
    src = Path(__file__).parents[1] / "src/dictation4me/desktop/portal.py"
    assert '"meta": "LOGO"' in src.read_text()


def test_windows_hotkey_accepts_meta():
    from dictation4me.desktop.windows import hotkey_codes
    flags, code = hotkey_codes("Ctrl+Meta+D")
    assert flags & 0x8 and flags & 0x2 and code == ord("D")


def test_invalid_save_writes_nothing(app_paths):
    config.merge(app_paths, {"ai": {"model": "kept"}})
    with pytest.raises(AppError):
        config.merge(app_paths, {"ai": {"model": "new"}, "quick": {"shortcut": "Hyper+L"}})
    assert config.load(app_paths)["ai"]["model"] == "kept"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="freedesktop launcher")
def test_launcher_keeps_virtualenv_interpreter(monkeypatch, tmp_path):
    base = tmp_path / "base/python3"
    base.parent.mkdir()
    base.write_text("")
    venv = tmp_path / "venv/bin/python"
    venv.parent.mkdir(parents=True)
    venv.symlink_to(base)
    monkeypatch.delenv("DICTATION4ME_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "executable", str(venv))
    onboarding.install_desktop(autostart=True)
    for entry in (tmp_path / "share/applications/org.bigbatmanorg.dictation4me.desktop",
                  tmp_path / "config/autostart/org.bigbatmanorg.dictation4me.desktop"):
        text = entry.read_text()
        assert f'Exec="{venv}" -m dictation4me' in text
        assert str(base) not in text


def test_service_spawned_as_own_systemd_unit_without_secret_values(app_paths, monkeypatch):
    config.merge(app_paths, {"ai": {"key_source": "environment", "key_reference": "MY_TOKEN"}})
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DICTATION4ME_NO_SYSTEMD", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    seen = {}

    def run(command, **kwargs):
        seen["command"], seen["env"] = command, kwargs["env"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    env = {"MY_TOKEN": "s3cret-value", "WAYLAND_DISPLAY": "wayland-0", "UI4ME_TOKEN": "host"}
    assert cli._spawn_systemd(app_paths, ["python", "-m", "dictation4me", "service", "run"], env)
    command = seen["command"]
    assert command[:3] == ["/usr/bin/systemd-run", "--user", "--quiet"]
    assert "--setenv=MY_TOKEN" in command and "--setenv=WAYLAND_DISPLAY" in command
    assert not any("s3cret-value" in part for part in command)
    assert not any("UI4ME" in part for part in command)
    assert command[-5:] == ["python", "-m", "dictation4me", "service", "run"]


def test_missing_portaudio_is_clean_error(app_paths, monkeypatch):
    import builtins

    from dictation4me.audio import AudioRecorder
    real_import = builtins.__import__

    def fail(name, *args, **kwargs):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(builtins, "__import__", fail)
    with pytest.raises(AppError) as info:
        AudioRecorder(app_paths, "r1", config.DEFAULTS["audio"]).start()
    assert info.value.code == "AUDIO_UNAVAILABLE"


def test_wayland_paste_prefers_portal_and_reports_failure(monkeypatch, tmp_path):
    from dictation4me.desktop import linux

    sent = []

    class Keyboard:
        def send_keys(self, codes):
            sent.append(codes)

    backend = linux.LinuxDesktop()
    backend.session = "wayland"
    monkeypatch.setattr(linux.time, "sleep", lambda _: None)
    monkeypatch.setattr(backend, "keyboard", lambda: Keyboard())
    assert backend.paste("Ctrl+V") == "sent"
    assert sent == [[29, 47]]

    class Denied:
        def send_keys(self, codes):
            raise AppError("PASTE_DENIED", "Keyboard access for automatic pasting was declined")

    monkeypatch.setattr(backend, "keyboard", lambda: Denied())
    assert backend.paste("Ctrl+Shift+V") == "blocked"  # no ydotool fallback without opt-in
    assert "declined" in backend.last_paste_error


def test_remote_desktop_token_is_private(tmp_path):
    from dictation4me.desktop.remote_desktop import RemoteDesktopKeyboard
    keyboard = RemoteDesktopKeyboard(tmp_path / "state" / "remote-desktop.token")
    assert not keyboard.authorized
    keyboard._save_token("tok-1")
    assert keyboard.authorized
    if sys.platform != "win32":
        assert (keyboard.token_path.stat().st_mode & 0o777) == 0o600
    keyboard.forget()
    assert not keyboard.authorized
