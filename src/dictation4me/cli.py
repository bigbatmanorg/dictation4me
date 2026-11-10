"""Public console entry point. --help and init --no-gui work without a display."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

from . import __version__, paths


def output_test(config, *, delay=3, text="Dictation4me test — Příliš žluťoučký kůň.") -> str:
    async def test():
        if sys.platform == "win32":
            from .desktop.windows import WindowsDesktop
            desktop = WindowsDesktop(config.output)
        else:
            from .desktop.linux import LinuxDesktop
            desktop = LinuxDesktop(config.output)
        try:
            await asyncio.sleep(max(0, delay))
            await desktop.copy(text)
            if config.output.auto_paste and config.output.backend != "clipboard":
                await asyncio.sleep(config.output.paste_delay_ms / 1000)
                if await desktop.read_clipboard() != text:
                    return "Clipboard changed; test paste skipped"
                await desktop.paste(config.output.paste_shortcut)
                await asyncio.sleep(0.4)
                return "Paste shortcut sent. Verify the sample text in your target field."
            return "Sample copied to clipboard; automatic paste is disabled"
        finally:
            await desktop.close()
    return asyncio.run(test())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dictation4me", description="Batch speech dictation for Linux and Windows")
    result.add_argument("--version", action="version", version=f"dictation4me {__version__}")
    sub = result.add_subparsers(dest="command")
    init = sub.add_parser("init", aliases=["onboard"], help="Create config, menu item and sign-in launcher; open settings")
    init.add_argument("--no-gui", action="store_true")
    init.add_argument("--no-autostart", action="store_true")
    init.add_argument("--install-deps", action="store_true", help="Offer system package installation (requires sudo on Linux)")
    for p in (init, sub.add_parser("setup-system", help="Install native prerequisites with confirmation")):
        p.add_argument("--enable-uinput", action="store_true")
        p.add_argument("--enable-debian-backports", action="store_true")
        p.add_argument("--uinput-group", action="store_true", help="Explicit fallback: dedicated uinput-only group")
        p.add_argument("--yes", action="store_true")
        p.add_argument("--dry-run", action="store_true")
    sub.add_parser("settings", help="Open TTK settings")
    sub.add_parser("launch", help="Start the resident service if not already running")
    run = sub.add_parser("run", help="Run the service in the foreground with visible logs")
    run.add_argument("--no-tray", action="store_true")
    run.add_argument("--no-hotkey", action="store_true")
    for command, helptext in {
        "toggle": "Start or stop recording (also suitable for desktop shortcuts)",
        "start-recording": "Start recording", "stop-recording": "Stop and transcribe",
        "cancel": "Cancel current operation; do not paste later",
        "retry": "Retry the last failed upload retained in memory",
        "copy-last": "Copy the last completed transcript",
        "paste-last": "Copy and send the paste shortcut for the last transcript",
        "status": "Show service state and desktop capabilities (JSON)",
        "reload": "Reload settings while idle", "stop": "Stop the resident service",
    }.items():
        sub.add_parser(command, help=helptext)
    doctor = sub.add_parser("doctor", aliases=["check"], help="Read-only configuration, desktop and audio diagnostics")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--no-audio", action="store_true")
    sub.add_parser("devices", aliases=["list-devices"], help="List audio input devices")
    out = sub.add_parser("test-output", help="Paste a harmless Unicode sample after a delay")
    out.add_argument("--delay", type=float, default=3)
    out.add_argument("--text", default="Dictation4me test — Příliš žluťoučký kůň.")
    stt = sub.add_parser("test-stt", help="Upload an existing WAV; print text; do not paste")
    stt.add_argument("wav", type=Path)
    sub.add_parser("paths", help="Show per-user configuration/state locations")
    remove = sub.add_parser("uninstall", help="Remove this app's desktop integration; preserve config unless --purge")
    remove.add_argument("--purge", action="store_true")
    return result


def _settings():
    try:
        from .ui.settings import show_settings
    except ImportError as exc:
        raise RuntimeError("Tk is missing. On Debian install python3-tk and use uv tool install --python /usr/bin/python3 .") from exc
    show_settings()


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    command = args.command or "launch"
    try:
        if command in ("init", "onboard", "setup-system"):
            from .onboarding import initialize, setup_system
            wants_system = command == "setup-system" or args.install_deps or args.enable_uinput or args.enable_debian_backports or args.uinput_group
            if wants_system or args.dry_run:
                setup_system(enable_uinput=args.enable_uinput,
                             enable_backports=args.enable_debian_backports,
                             dedicated_group=args.uinput_group, yes=args.yes, dry_run=args.dry_run)
            if command == "setup-system" or args.dry_run:
                return 0
            _, entries = initialize(autostart=False if args.no_autostart else None)
            print(f"Configuration: {paths.config_file()}")
            for entry in entries:
                print(f"Installed: {entry}")
            print("Configure your speech URL, model and credentials. Default shortcut: Ctrl+Alt+D.")
            if args.no_gui:
                print("Next: dictation4me settings (or edit config.toml), then dictation4me launch")
            else:
                _settings()
        elif command == "settings":
            _settings()
        elif command == "paths":
            print(json.dumps({"config": str(paths.config_file()), "data": str(paths.data_dir()),
                              "state": str(paths.state_dir()), "runtime": str(paths.runtime_dir())}, indent=2))
        elif command in ("doctor", "check"):
            from .diagnostics import diagnose
            rows = diagnose(audio=not args.no_audio)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    print(f"[{row['level'].upper():4}] {row['check']}: {row['detail']}")
            return 2 if any(row["level"] == "fail" for row in rows) else 0
        elif command in ("devices", "list-devices"):
            from .audio import list_devices
            for device in list_devices():
                print(f"{'*' if device['default'] else ' '} index:{device['index']}  {device['name']}"
                      f" [{device['host_api']}; {device['sample_rate']:g} Hz]")
        elif command == "launch":
            from .app import launch
            if not paths.config_file().exists():
                from .onboarding import initialize
                initialize()
                _settings()
            else:
                result = launch()
                print(f"Dictation4me is running: {result.get('state', 'ready')}")
        elif command == "run":
            from .app import Service
            from .ipc import AlreadyRunning
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
            # Do not enable httpx/httpcore debug output: URLs and headers may be sensitive.
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)
            async def serve():
                service = Service(no_tray=args.no_tray, no_hotkey=args.no_hotkey)
                await service.run()
            try:
                asyncio.run(serve())
            except AlreadyRunning as exc:
                print(str(exc))
        elif command == "test-output":
            from .config import load
            print(f"Focus your target field now. Test in {args.delay:g} seconds.", flush=True)
            print(output_test(load(), delay=args.delay, text=args.text))
        elif command == "test-stt":
            from .config import load
            from .stt import transcribe
            if args.wav.stat().st_size > 24 * 1024 * 1024:
                raise ValueError("WAV exceeds 24 MiB")
            print(asyncio.run(transcribe(args.wav.read_bytes(), load().service)))
        elif command == "uninstall":
            from .ipc import request
            from .onboarding import remove_integration
            try:
                request("shutdown")
                for _ in range(50):
                    time.sleep(0.1)
                    try:
                        request("status", timeout=0.1)
                    except ConnectionError:
                        break
            except ConnectionError:
                pass
            for path in remove_integration(purge=args.purge):
                print(f"Removed: {path}")
            print("Desktop integration removed. To remove the Python tool: uv tool uninstall dictation4me")
            print("System packages, uinput rules and backports are not removed automatically; see README.")
        else:
            from .ipc import request
            action = "shutdown" if command == "stop" else command
            if command in ("toggle", "start-recording"):
                from .app import launch
                launch()
            result = request(action)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 2
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"dictation4me: {exc}", file=sys.stderr)
        return 2


def gui_main() -> None:
    """Windowed executable used by desktop launchers on Windows."""
    result = main()
    if result and sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "Dictation4me could not start. Run dictation4me doctor in a terminal.", "Dictation4me", 0x10)
