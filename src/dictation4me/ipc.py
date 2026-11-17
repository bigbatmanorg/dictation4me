"""Bounded, token-authenticated loopback JSON RPC. Not HTTP and not a public API."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import socketserver
import threading
import uuid
from pathlib import Path

from .errors import AppError
from .paths import Paths, atomic_text

MAX_REQUEST = 1024 * 1024
MAX_REPLY = 2 * 1024 * 1024


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                self.handle.write(b"0")
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.close()
            raise AppError("ALREADY_RUNNING", "Resident service is already running") from exc

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


class _TCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16


class RpcServer:
    def __init__(self, paths: Paths, callback):
        self.paths = paths.init()
        self.callback = callback
        self.token = secrets.token_hex(32)
        self.lock = InstanceLock(paths.lock_file)
        self.server = None
        self.slots = threading.BoundedSemaphore(12)
        self._stop_requested = threading.Event()

    def serve(self):
        self.lock.acquire()
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                with owner.slots:
                    self.connection.settimeout(12)
                    try:
                        line = self.rfile.readline(MAX_REQUEST + 1)
                        if len(line) > MAX_REQUEST:
                            raise AppError("TOO_LARGE", "IPC message exceeds limit")
                        if not line.endswith(b"\n"):
                            raise AppError("INVALID", "Incomplete IPC request")
                        req = json.loads(line)
                        if (not isinstance(req, dict) or req.get("protocol") != 1 or
                            not isinstance(req.get("token"), str) or
                            not hmac.compare_digest(req["token"], owner.token)):
                            raise AppError("UNAUTHORIZED", "Invalid local service credentials")
                        args = req.get("params", {})
                        if not isinstance(args, dict) or not isinstance(req.get("action"), str):
                            raise AppError("INVALID", "Invalid RPC request")
                        result = owner.callback(req["action"], args)
                        output = {"ok": True, "result": result, "id": req.get("id")}
                    except AppError as exc:
                        output = {"ok": False, "error": {"code": exc.code, "message": str(exc),
                                                         "hint": exc.hint}}
                    except (ValueError, TypeError, json.JSONDecodeError):
                        output = {"ok": False, "error": {"code": "INVALID", "message": "Malformed IPC message"}}
                    except Exception:
                        # Exception strings may contain note content or provider URLs.
                        import traceback
                        traceback.print_exc()  # type + stack to service.log for troubleshooting
                        output = {"ok": False, "error": {"code": "INTERNAL", "message": "Internal service error"}}
                    encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
                    if len(encoded) > MAX_REPLY:
                        encoded = b'{"ok":false,"error":{"code":"TOO_LARGE","message":"Result exceeds IPC limit"}}\n'
                    try:
                        self.wfile.write(encoded)
                        self.wfile.flush()
                    except OSError:
                        pass

        try:
            self.server = _TCPServer(("127.0.0.1", 0), Handler)
            port = self.server.server_address[1]
            atomic_text(self.paths.endpoint_file, json.dumps({"protocol": 1, "port": port,
                                                                 "pid": os.getpid(), "token": self.token}))
            self.server.serve_forever(poll_interval=.2)
        finally:
            if self.server:
                self.server.server_close()
            try:
                endpoint = json.loads(self.paths.endpoint_file.read_text(encoding="utf-8"))
                if endpoint.get("token") == self.token:
                    self.paths.endpoint_file.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            self.lock.close()

    def stop(self):
        if self.server and not self._stop_requested.is_set():
            self._stop_requested.set()
            # shutdown() must be called from a different thread than serve_forever()
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def invoke(paths: Paths, action: str, params: dict | None = None, *, timeout: float = 8,
           request_id: str | None = None):
    try:
        ep = json.loads(paths.endpoint_file.read_text(encoding="utf-8"))
        port = ep["port"]
        token = ep["token"]
        if ep.get("protocol") != 1 or type(port) is not int or not 0 < port < 65536:
            raise ValueError("bad endpoint")
        message = json.dumps({"protocol": 1, "id": request_id or str(uuid.uuid4()),
                              "token": token, "action": action, "params": params or {}},
                             ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(message) > MAX_REQUEST:
            raise AppError("TOO_LARGE", "Local message exceeds IPC limit")
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
            client.settimeout(timeout)
            client.sendall(message)
            with client.makefile("rb") as stream:
                raw = stream.readline(MAX_REPLY + 1)
        if not raw or len(raw) > MAX_REPLY:
            raise ValueError("missing/oversized response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("bad response")
    except AppError:
        raise
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AppError("SERVICE_UNAVAILABLE", "The Dictation4me resident service is not reachable") from exc
    if not result.get("ok"):
        error = result.get("error") or {}
        raise AppError(str(error.get("code", "SERVICE")), str(error.get("message", "Service request failed")),
                       hint=error.get("hint") or None)
    return result.get("result")
