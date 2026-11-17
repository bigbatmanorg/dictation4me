"""Non-privileged desktop launchers; never grants uinput or installs OS packages."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .errors import AppError
from .paths import atomic_text


def _desktop_arg(s: str) -> str:
    # Freedesktop Exec is not a shell. Quote literal paths with double quotes.
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _linux_paths() -> tuple[Path, Path]:
    home = Path.home()
    return (Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "applications",
            Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "autostart")


def install_desktop(*, autostart: bool = False, dry_run: bool = False) -> dict:
    if os.environ.get("DICTATION4ME_HOME"):
        return {"skipped": True, "reason": "Isolated DICTATION4ME_HOME never changes system launchers"}
    # Do not resolve(): a virtualenv/uv-tool interpreter is a symlink to the base
    # Python, and following it loses the environment where dictation4me lives.
    interpreter = str(Path(sys.executable).absolute())
    if sys.platform == "win32":
        programs = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs"
        startup = programs.parent / "Startup"
        targets = [programs / "Dictation4me.lnk"] + ([startup / "Dictation4me Service.lnk"] if autostart else [])
        if dry_run:
            return {"would_create": [str(p) for p in targets], "python": interpreter}
        # PowerShell is used solely to create .lnk files. No interpolated shell command.
        def make_shortcut(path: Path, arguments: str):
            path.parent.mkdir(parents=True, exist_ok=True)
            script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:D4M_LINK);"
                      "$s.TargetPath=$env:D4M_PYTHON; $s.Arguments=$env:D4M_ARGS;"
                      "$s.WorkingDirectory=$env:USERPROFILE; $s.Save()")
            env = dict(os.environ, D4M_LINK=str(path), D4M_PYTHON=interpreter, D4M_ARGS=arguments)
            outcome = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                     env=env, check=False, capture_output=True, timeout=20)
            if outcome.returncode:
                raise AppError("INSTALL", "Could not create a Windows Start Menu shortcut")
        make_shortcut(programs / "Dictation4me.lnk", "-m dictation4me gui")
        if autostart:
            make_shortcut(startup / "Dictation4me Service.lnk", "-m dictation4me service run")
        return {"created": [str(p) for p in targets]}
    if sys.platform.startswith("linux"):
        apps, startup = _linux_paths()
        cmd_gui = _desktop_arg(interpreter) + " -m dictation4me gui"
        cmd_service = _desktop_arg(interpreter) + " -m dictation4me service run"
        entry = ("[Desktop Entry]\nType=Application\nName=Dictation4me\n"
                 "Comment=Local-first AI notes and dictation\n"
                 f"Exec={cmd_gui}\nTryExec={interpreter}\nTerminal=false\nCategories=Utility;Office;\n"
                 "StartupNotify=true\n")
        autostart_entry = ("[Desktop Entry]\nType=Application\nName=Dictation4me Service\n"
                           f"Exec={cmd_service}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
        menu_file = apps / "org.bigbatmanorg.dictation4me.desktop"
        auto_file = startup / "org.bigbatmanorg.dictation4me.desktop"
        if dry_run:
            return {"would_create": [str(menu_file)] + ([str(auto_file)] if autostart else []),
                    "shortcut_fallback": "Assign a KDE custom shortcut to: dictation4me toggle"}
        atomic_text(menu_file, entry)
        if autostart:
            atomic_text(auto_file, autostart_entry)
        return {"created": [str(menu_file)] + ([str(auto_file)] if autostart else []),
                "shortcut_fallback": "Assign a KDE custom shortcut to: dictation4me toggle"}
    raise AppError("UNSUPPORTED_PLATFORM", "Only Linux and Windows desktop launchers are supported")


def uninstall_desktop() -> dict:
    if os.environ.get("DICTATION4ME_HOME"):
        return {"skipped": True, "reason": "Isolated profile has no system launchers"}
    if sys.platform == "win32":
        programs = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs"
        targets = [programs / "Dictation4me.lnk", programs.parent / "Startup/Dictation4me Service.lnk"]
    else:
        apps, startup = _linux_paths()
        targets = [apps / "org.bigbatmanorg.dictation4me.desktop",
                   startup / "org.bigbatmanorg.dictation4me.desktop"]
    for target in targets:
        target.unlink(missing_ok=True)
    return {"removed": [str(p) for p in targets]}
