"""Idempotent, per-user launchers and explicitly authorized system prerequisites."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from importlib.resources import files

from . import APP_ID, paths
from .config import Config, load, save


def interpreter(*, graphical=False) -> str:
    # Do NOT resolve the symlink: that would escape uv's isolated environment.
    python = Path(sys.executable).absolute()
    if graphical and sys.platform == "win32":
        windowed = python.with_name("pythonw.exe")
        if windowed.exists():
            python = windowed
    return str(python)


def desktop_quote(arg: str) -> str:
    if any(c in arg for c in ("\n", "\r", "\0")):
        raise ValueError("Launcher arguments cannot contain control characters")
    # .desktop string unescaping happens before Exec argument parsing.
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return '"' + escaped.replace("\\", "\\\\").replace("%", "%%") + '"'


def launcher_command(action: str, *, graphical=False) -> list[str]:
    return [interpreter(graphical=graphical), "-m", "dictation4me", action]


def desktop_entry(action: str = "launch", *, autostart: bool = False) -> str:
    cmd = " ".join(map(desktop_quote, launcher_command(action)))
    icon = str(paths.data_dir() / "dictation4me.svg")
    text = ("[Desktop Entry]\nType=Application\nVersion=1.0\nName=Dictation4me\n"
            "Comment=Record speech and paste the transcription\n"
            f"Exec={cmd}\nIcon={icon}\nTerminal=false\nCategories=Utility;AudioVideo;\n"
            "StartupNotify=false\n")
    if autostart:
        return text + "X-GNOME-Autostart-enabled=true\n"
    return (text + "Actions=Settings;Toggle;\n\n[Desktop Action Settings]\nName=Settings\nExec="
            + " ".join(map(desktop_quote, launcher_command("settings")))
            + "\n\n[Desktop Action Toggle]\nName=Start / stop dictation\nExec="
            + " ".join(map(desktop_quote, launcher_command("toggle"))) + "\n")


def _linux_paths() -> tuple[Path, Path]:
    # A testing/config override also redirects integration files, preventing a
    # test run from touching the real desktop environment.
    if os.environ.get("DICTATION4ME_HOME"):
        root = paths.config_dir() / "integration"
        return root / "applications" / f"{APP_ID}.desktop", root / "autostart" / f"{APP_ID}.desktop"
    menu = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications"
    auto = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
    return menu / f"{APP_ID}.desktop", auto / f"{APP_ID}.desktop"


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_shortcut(path: Path, action: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = launcher_command(action, graphical=True)
    script = (
        "$ErrorActionPreference='Stop'; $w=New-Object -ComObject WScript.Shell; "
        f"$s=$w.CreateShortcut({_ps_literal(str(path))}); "
        f"$s.TargetPath={_ps_literal(command[0])}; "
        f"$s.Arguments={_ps_literal(subprocess.list2cmdline(command[1:]))}; "
        f"$s.WorkingDirectory={_ps_literal(str(paths.data_dir()))}; "
        f"$s.IconLocation={_ps_literal(str(paths.data_dir() / 'dictation4me.ico'))}; "
        "$s.Description='Dictation4me'; $s.Save()"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                   check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)


def _windows_paths() -> tuple[Path, Path, Path]:
    if os.environ.get("DICTATION4ME_HOME"):
        root = paths.config_dir() / "integration"
    else:
        root = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    return root / "Dictation4me.lnk", root / "Dictation4me Settings.lnk", root / "Startup/Dictation4me.lnk"


def install_integration(config: Config) -> list[Path]:
    paths.private_dir(paths.data_dir())
    for name in ("dictation4me.svg", "dictation4me.ico"):
        target = paths.data_dir() / name
        target.write_bytes(files("dictation4me.assets").joinpath(name).read_bytes())
    result = []
    if sys.platform == "win32":
        menu, settings, auto = _windows_paths()
        _windows_shortcut(menu, "launch")
        _windows_shortcut(settings, "settings")
        result += [menu, settings]
        if config.desktop.autostart:
            _windows_shortcut(auto, "launch")
            result.append(auto)
        else:
            auto.unlink(missing_ok=True)
    elif sys.platform == "linux":
        menu, auto = _linux_paths()
        paths.atomic_write(menu, desktop_entry(), private=False)
        result.append(menu)
        if config.desktop.autostart:
            paths.atomic_write(auto, desktop_entry(autostart=True), private=False)
            result.append(auto)
        else:
            auto.unlink(missing_ok=True)
        update = shutil.which("update-desktop-database")
        if update:
            subprocess.run([update, str(menu.parent)], check=False, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
    else:
        raise RuntimeError("Only Linux and Windows are supported in this release")
    return result


def initialize(*, autostart: bool | None = None) -> tuple[Config, list[Path]]:
    cfg = load(missing_ok=True)
    if autostart is not None:
        cfg.desktop.autostart = autostart
    save(cfg)
    return cfg, install_integration(cfg)


def remove_integration(*, purge=False) -> list[Path]:
    known = _windows_paths() if sys.platform == "win32" else _linux_paths()
    removed = []
    for path in known:
        if path.exists():
            path.unlink()
            removed.append(path)
    if purge:
        # Never recursively delete a user-supplied DICTATION4ME_HOME directory.
        for path in (paths.config_file(), paths.state_dir() / "service.log",
                     paths.data_dir() / "dictation4me.svg", paths.data_dir() / "dictation4me.ico"):
            path.unlink(missing_ok=True)
    return removed


def os_release() -> dict[str, str]:
    try:
        result = {}
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                result[key] = value.strip('"')
        return result
    except OSError:
        return {}


def _root_command(args: list[str], *, text: str | None = None) -> None:
    command = args if os.geteuid() == 0 else ["sudo", *args]
    print("+ " + shlex.join(command), flush=True)
    subprocess.run(command, input=text, text=True, check=True)


def setup_system(*, enable_uinput=False, enable_backports=False, dedicated_group=False,
                 yes=False, dry_run=False) -> None:
    if sys.platform != "linux":
        print("Windows: Python dependencies are installed by uv; microphone privacy permission may be required.")
        return
    distro = os_release()
    family = distro.get("ID", "") + " " + distro.get("ID_LIKE", "")
    is_trixie = distro.get("ID") == "debian" and distro.get("VERSION_CODENAME") == "trixie"
    if enable_backports and not is_trixie:
        raise RuntimeError("The backports switch is only supported for Debian 13 (trixie)")
    if "debian" in family or "ubuntu" in family:
        commands = [["apt-get", "update"], ["apt-get", "install", "-y", "libportaudio2", "libasound2-plugins", "python3-tk",
                                            "wl-clipboard", "xclip", "xdotool"]]
        # Debian 13 ships ydotool 1.x in official trixie-backports, not main.
        if is_trixie and enable_backports:
            commands.append(["apt-get", "install", "-y", "-t", "trixie-backports", "ydotool"])
        elif not is_trixie:
            commands.append(["apt-get", "install", "-y", "ydotool"])
    elif "fedora" in family or "rhel" in family:
        commands = [["dnf", "install", "-y", "portaudio", "python3-tkinter", "wl-clipboard", "xclip", "xdotool", "ydotool"]]
    elif "arch" in family:
        commands = [["pacman", "-S", "--needed", "portaudio", "tk", "wl-clipboard", "xclip", "xdotool", "ydotool"]]
    elif "suse" in family:
        commands = [["zypper", "--non-interactive", "install", "libportaudio2", "python3-tk", "wl-clipboard", "xclip", "xdotool", "ydotool"]]
    else:
        raise RuntimeError("Unknown distribution; install PortAudio, Tk, wl-clipboard and ydotool manually")
    print("System package changes (administrator authorization required):")
    for command in commands:
        print("  " + shlex.join(command))
    if enable_backports:
        print("  Add official Debian trixie-backports source, signed by Debian's archive keyring.")
    if enable_uinput:
        print("  Allow the active desktop user to write /dev/uinput (virtual keyboard injection).")
    if dedicated_group:
        if not enable_uinput:
            raise RuntimeError("--uinput-group requires --enable-uinput")
        print("  Add this user to a dedicated dictation4me-input group, NOT the general input group.")
    if dry_run:
        return
    if not yes and input("Apply these system changes? [y/N] ").strip().lower() not in ("y", "yes"):
        print("No system changes made.")
        return
    if enable_backports:
        text = ("Types: deb\nURIs: https://deb.debian.org/debian\nSuites: trixie-backports\n"
                "Components: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n")
        _root_command(["tee", "/etc/apt/sources.list.d/dictation4me-backports.sources"], text=text)
    for command in commands:
        _root_command(command)
    if is_trixie and not enable_backports and not shutil.which("ydotool"):
        print("For Wayland paste, rerun with --enable-debian-backports --enable-uinput, or use clipboard-only mode.")
    if enable_uinput:
        _root_command(["modprobe", "uinput"])
        _root_command(["tee", "/etc/modules-load.d/dictation4me-uinput.conf"], text="uinput\n")
        group = 'GROUP="root"'
        if dedicated_group:
            import pwd
            username = pwd.getpwuid(os.getuid()).pw_name
            if username == "root":
                raise RuntimeError("Run this command as the desktop user, not through sudo")
            _root_command(["groupadd", "-f", "dictation4me-input"])
            _root_command(["usermod", "-aG", "dictation4me-input", username])
            group = 'GROUP="dictation4me-input"'
        # Must sort before 73-seat-late.rules for logind's uaccess builtin.
        rule = f'KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", {group}, TAG+="uaccess", OPTIONS+="static_node=uinput"\n'
        _root_command(["tee", "/etc/udev/rules.d/70-dictation4me-uinput.rules"], text=rule)
        _root_command(["udevadm", "control", "--reload-rules"])
        _root_command(["udevadm", "trigger", "--subsystem-match=misc", "--sysname-match=uinput"])
        print("Log out/in if access is not active yet. Never make /dev/uinput world-writable.")
