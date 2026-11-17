"""User CLI for installation, resident service, recovery, testing and export."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import __version__, config, desktop, providers
from .diagnostics import doctor
from .errors import AppError
from .ipc import invoke
from .onboarding import install_desktop, uninstall_desktop
from .paths import Paths
from .portable import backup, export_markdown, restore
from .storage import Notebook

SERVICE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH", "VIRTUAL_ENV",
               "DICTATION4ME_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
               "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY",
               "DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "YDOTOOL_SOCKET",
               "PULSE_SERVER", "PIPEWIRE_REMOTE", "DICTATION4ME_NO_HOTKEY",
               "DICTATION4ME_NO_TRAY")


def _service_env(paths: Paths) -> dict:
    env = dict(os.environ)
    # The resident service is a separate interpreter. Preserve the package's
    # import root when running directly from an unpacked source checkout or
    # under pytest, without depending on the caller's current directory.
    package_root = str(Path(__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH", "")
    if package_root not in existing.split(os.pathsep):
        env["PYTHONPATH"] = package_root + (os.pathsep + existing if existing else "")
    return env


def _credential_env_names(paths: Paths) -> list[str]:
    try:
        settings = config.load(paths)
    except AppError:
        return []
    return sorted({settings[s]["key_reference"] for s in ("stt", "ai")
                   if settings[s]["key_source"] == "environment" and settings[s]["key_reference"]})


def _systemd_unit(paths: Paths) -> str:
    import hashlib
    return "dictation4me-service-" + hashlib.sha256(str(paths.runtime).encode()).hexdigest()[:10]


def _spawn_systemd(paths: Paths, argv: list[str], env: dict) -> bool:
    """Start the service as its own transient user unit.

    The notebook backend runs inside the NativeHTMLKit host's systemd unit; a
    plain child process shares that cgroup and is killed when the window closes.
    Variables are passed by name (``--setenv=NAME``) so values, including API
    keys, are never placed on a command line.
    """
    if not sys.platform.startswith("linux") or os.environ.get("DICTATION4ME_NO_SYSTEMD"):
        return False
    tool = shutil.which("systemd-run")
    if not tool:
        return False
    log = paths.state / "service.log"
    names = [n for n in SERVICE_ENV + tuple(_credential_env_names(paths)) if n in env]
    command = [tool, "--user", "--quiet", "--collect", "--unit", _systemd_unit(paths),
               "-p", f"StandardOutput=append:{log}", "-p", f"StandardError=append:{log}",
               "--working-directory", str(Path.home())]
    command += [f"--setenv={n}" for n in names] + ["--"] + argv
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def ensure_service(paths: Paths, *, timeout: float = 8) -> dict:
    try:
        return invoke(paths, "status", timeout=.5)
    except AppError:
        pass
    paths.init()
    argv = [sys.executable, "-m", "dictation4me", "service", "run"]
    env = _service_env(paths)
    if not _spawn_systemd(paths, argv, env):
        log = (paths.state / "service.log").open("ab")
        flags = 0
        kwargs = {}
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             close_fds=(sys.platform != "win32"), creationflags=flags, env=env,
                             **kwargs)
        finally:
            log.close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return invoke(paths, "status", timeout=.5)
        except AppError:
            time.sleep(.15)
    raise AppError("STARTUP", "Service did not become available; inspect private state/service.log")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dictation4me", description="Native AI notes and dictation")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Write default configuration; optional desktop integration")
    init.add_argument("--dry-run", action="store_true")
    init.add_argument("--autostart", action="store_true")
    init.add_argument("--no-desktop", action="store_true")
    sub.add_parser("gui", help="Open NativeHTMLKit notebook")
    sub.add_parser("quick-review", help="Open notebook on Quick tab")
    sub.add_parser("live", help="Live transcript window (opened automatically when realtime is on)")
    sub.add_parser("toggle", help="External desktop shortcut: start, stop or deliver")
    sub.add_parser("cancel", help="Cancel active capture (audio remains recoverable)")
    sub.add_parser("copy-last")
    sub.add_parser("paste-last", help="Explicit repeat may produce duplicate text")
    service = sub.add_parser("service")
    service.add_argument("operation", choices=["start", "run", "stop", "status", "restart"])
    doc = sub.add_parser("doctor")
    doc.add_argument("--json", action="store_true")
    sub.add_parser("devices")
    config_cmd = sub.add_parser("config")
    config_cmd.add_argument("operation", choices=["show", "validate"])
    tests = sub.add_parser("test")
    tests.add_argument("kind", choices=["microphone", "stt", "ai", "clipboard", "paste"])
    tests.add_argument("--file", help="WAV recording for explicit STT test")
    tests.add_argument("--i-understand", action="store_true", help="Enable input simulation test")
    tests.add_argument("--seconds", type=int, default=2)
    backup_cmd = sub.add_parser("backup")
    backup_cmd.add_argument("--output", required=True)
    backup_cmd.add_argument("--with-audio", action=argparse.BooleanOptionalAction, default=True)
    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("--output", required=True)
    export_cmd.add_argument("--with-audio", action="store_true")
    restore_cmd = sub.add_parser("restore")
    restore_cmd.add_argument("--input", required=True)
    restore_cmd.add_argument("--destination", required=True)
    rec = sub.add_parser("recover")
    rec.add_argument("operation", choices=["list", "retry", "salvage", "delete"])
    rec.add_argument("id", nargs="?")
    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--purge", action="store_true")
    uninstall.add_argument("--confirm-purge", action="store_true")
    return p


def output(value):
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif value is not None:
        print(value)


def run_test(kind: str, args, paths: Paths):
    settings = config.load(paths)
    if kind == "microphone":
        from .audio import AudioRecorder
        ident = uuid.uuid4().hex
        recording = AudioRecorder(paths, ident, {**settings["audio"],
                                                    "max_seconds": max(3, args.seconds+2)})
        recording.start()
        time.sleep(max(1, min(10, args.seconds)))
        result = recording.stop()
        # This is explicit test audio, not a note; delete only after confirmed finalization.
        if result.get("file_relpath"):
            paths.media_path(result["file_relpath"]).unlink(missing_ok=True)
        else:
            (paths.spool / (ident + ".part")).unlink(missing_ok=True)
        return {"microphone": "captured", "duration_ms": result["duration_ms"],
                "error": result.get("error_code")}
    if kind == "stt":
        if not args.file:
            raise AppError("INVALID", "Explicit STT test requires --file path/to/sample.wav")
        config.require_provider_consent(settings, "stt")
        return providers.transcribe(Path(args.file), settings["stt"])
    if kind == "ai":
        preset = {"instruction": "Reply with the single word OK.", "preserve_language": False,
                  "output_format": "plain"}
        config.require_provider_consent(settings, "ai")
        return {"reply": providers.transform("test", settings["ai"], preset)}
    backend = desktop.clipboard()
    if kind == "clipboard":
        text = "Dictation4me clipboard test — žluťoučký 📝"
        backend.copy(text)
        return {"copied": backend.read() == text}
    if kind == "paste":
        if not args.i_understand:
            raise AppError("CONSENT", "Paste test requires --i-understand; it sends synthetic keys")
        text = "Dictation4me paste test"
        backend.copy(text)
        print("Select a disposable target field now; paste attempt in 3 seconds.", flush=True)
        time.sleep(3)
        target = backend.foreground_token()
        if desktop.session_type() == "windows":
            status = backend.paste(settings["quick"]["paste_shortcut"], expected_target=target)
        else:
            status = backend.paste(settings["quick"]["paste_shortcut"],
                                   unsafe_wayland=settings["quick"]["paste_to_current_focus_unsafe_wayland"])
        return {"paste_shortcut": status, "target_accepted": "not verifiable"}


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    paths = Paths.discover()
    try:
        if args.command == "init":
            from .presets import Presets
            if args.dry_run:
                output({"config": str(paths.config_file),
                        "would_write_config": not paths.config_file.exists(),
                        "desktop": None if args.no_desktop else install_desktop(autostart=args.autostart, dry_run=True)})
                return 0
            paths.init()
            if not paths.config_file.exists():
                config.save(paths, config.DEFAULTS)
            Presets(paths)
            result = {"config": str(paths.config_file), "data": str(paths.data), "preserved": True}
            if not args.no_desktop:
                result["desktop"] = install_desktop(autostart=args.autostart)
            output(result)
            return 0
        if args.command == "service":
            if args.operation == "run":
                from .service import run
                return run()
            if args.operation == "start":
                output(ensure_service(paths))
            elif args.operation == "status":
                output(invoke(paths, "status"))
            elif args.operation == "stop":
                output(invoke(paths, "service.stop"))
            elif args.operation == "restart":
                try:
                    invoke(paths, "service.stop")
                except AppError:
                    pass
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        invoke(paths, "status", timeout=.4)
                    except AppError:
                        break
                    time.sleep(.2)
                time.sleep(.5)  # let the transient unit be collected
                output(ensure_service(paths))
            return 0
        if args.command == "live":
            from .gui import live_main
            return live_main()
        if args.command in ("gui", "quick-review"):
            from .gui import main as gui_main
            return gui_main(initial_tab="quick" if args.command == "quick-review" else "notes")
        if args.command == "doctor":
            report = doctor(paths)
            output(report)
            return 0
        if args.command == "config":
            data = config.load(paths)
            output(data if args.operation == "show" else {"valid": True, "sections": list(data)})
            return 0
        if args.command == "devices":
            from .audio import devices
            output(devices())
            return 0
        if args.command == "test":
            output(run_test(args.kind, args, paths))
            return 0
        if args.command == "restore":
            output(restore(Path(args.input).resolve(), Path(args.destination).resolve()))
            return 0
        if args.command == "uninstall":
            try:
                invoke(paths, "service.stop", timeout=1)
            except AppError:
                pass
            output(uninstall_desktop())
            if args.purge:
                if not args.confirm_purge:
                    raise AppError("CONFIRMATION", "To permanently remove all user data, also pass --confirm-purge")
                # Never delete a home directory, arbitrary parent, or non-app-owned paths.
                for path in (paths.config, paths.data, paths.state, paths.runtime):
                    if path.exists() and path.name.lower() in ("dictation4me", "config", "data", "state", "runtime"):
                        shutil.rmtree(path)
                output({"purged": True})
            return 0
        if args.command in ("backup", "export"):
            # Consistent backup must run in resident process? SQLite backup is safe
            # concurrently, but a separate reader avoids racing UI state writes.
            store = Notebook(paths)
            try:
                if args.command == "backup":
                    output(backup(store, Path(args.output).resolve(), with_audio=args.with_audio))
                else:
                    output(export_markdown(store, Path(args.output).resolve(), with_audio=args.with_audio))
            finally:
                store.close()
            return 0
        if args.command == "toggle":
            ensure_service(paths)
            output(invoke(paths, "capture.toggle"))
            return 0
        if args.command == "cancel":
            output(invoke(paths, "capture.cancel"))
            return 0
        if args.command == "copy-last":
            output(invoke(paths, "quick.copy"))
            return 0
        if args.command == "paste-last":
            output(invoke(paths, "quick.deliver", {"allow_repeat": True}))
            return 0
        if args.command == "recover":
            if args.operation == "list":
                output(invoke(paths, "recordings.recovery"))
            else:
                if not args.id:
                    raise AppError("INVALID", "Recovery retry/delete requires a recording ID")
                action = {"retry": "recordings.retry", "delete": "recordings.delete",
                          "salvage": "recordings.salvage"}[args.operation]
                output(invoke(paths, action, {"id": args.id}))
            return 0
        return 2
    except AppError as exc:
        print(f"ERROR [{exc.code}]: {exc}", file=sys.stderr)
        if exc.hint:
            print(f"  How to fix: {exc.hint}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
