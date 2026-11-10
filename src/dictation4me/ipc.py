"""Authenticated local JSON control channel, not an HTTP server or pickle socket."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import sys

from . import paths

MAX_MESSAGE = 65536


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, directory: Path | None = None):
        self.path = paths.private_dir(directory or paths.runtime_dir()) / "instance.lock"
        self.file = None

    def acquire(self) -> None:
        self.file = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise AlreadyRunning("Dictation4me is already running") from exc

    def close(self) -> None:
        if self.file:
            self.file.close()
            self.file = None
        # Never unlink a live locking inode: contenders may already have it open.


class Server:
    def __init__(self, handler, directory: Path | None = None):
        self.handler = handler
        self.directory = paths.private_dir(directory or paths.runtime_dir())
        self.path = self.directory / "control.json"
        self.token = secrets.token_hex(32)
        self.server = None
        self.lock = InstanceLock(self.directory)
        self.clients: set[asyncio.Task] = set()
        self.slots = asyncio.Semaphore(8)

    async def start(self) -> None:
        self.lock.acquire()
        try:
            self.server = await asyncio.start_server(self._client, "127.0.0.1", 0, limit=MAX_MESSAGE)
            port = self.server.sockets[0].getsockname()[1]
            paths.atomic_write(self.path, json.dumps({"version": 1, "port": port,
                                                      "token": self.token, "pid": os.getpid()}))
        except BaseException:
            self.lock.close()
            raise

    async def _client(self, reader, writer) -> None:
        task = asyncio.current_task()
        self.clients.add(task)
        try:
            async with asyncio.timeout(10):
                async with self.slots:
                    line = await reader.readline()
                    if len(line) > MAX_MESSAGE:
                        raise ValueError("oversized request")
                    data = json.loads(line)
                    if not isinstance(data, dict) or not isinstance(data.get("token"), str):
                        result = {"ok": False, "error": "Unauthorized"}
                    elif not hmac.compare_digest(data["token"], self.token):
                        result = {"ok": False, "error": "Unauthorized"}
                    elif not isinstance(data.get("action"), str):
                        result = {"ok": False, "error": "Invalid action"}
                    else:
                        result = await self.handler(data["action"])
                    writer.write(json.dumps(result).encode() + b"\n")
                    await writer.drain()
        except (ValueError, OSError, TimeoutError, asyncio.CancelledError):
            pass
        finally:
            self.clients.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self.clients):
            task.cancel()
        if self.clients:
            await asyncio.gather(*self.clients, return_exceptions=True)
        try:
            if self.path.exists() and json.loads(self.path.read_text())["token"] == self.token:
                self.path.unlink()
        except (OSError, ValueError, KeyError):
            pass
        self.lock.close()


def request(action: str, *, directory: Path | None = None, timeout: float = 5) -> dict:
    file = (directory or paths.runtime_dir()) / "control.json"
    try:
        endpoint = json.loads(file.read_text(encoding="utf-8"))
        port, token = endpoint["port"], endpoint["token"]
        if type(port) is not int or not 0 < port <= 65535 or not isinstance(token, str):
            raise ValueError("Invalid control endpoint")
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
            conn.sendall(json.dumps({"token": token, "action": action}).encode() + b"\n")
            with conn.makefile("rb") as stream:
                line = stream.readline(MAX_MESSAGE + 1)
            if len(line) > MAX_MESSAGE:
                raise ValueError("oversized response")
            result = json.loads(line)
            if not isinstance(result, dict):
                raise ValueError("Invalid response")
            return result
    except (OSError, ValueError, KeyError) as exc:
        raise ConnectionError("Dictation4me is not running; run dictation4me launch") from exc
