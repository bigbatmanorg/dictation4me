import os
import threading
import time

import pytest

from dictation4me.errors import AppError
from dictation4me.ipc import RpcServer, invoke


def test_authenticated_ipc_and_unknown_action(app_paths):
    server = RpcServer(app_paths, lambda action, params: {"action": action, "args": params})
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    try:
        end = time.monotonic()+2
        while not app_paths.endpoint_file.exists() and time.monotonic() < end:
            time.sleep(.01)
        assert invoke(app_paths, "hello", {"word": "žluťoučký"}) == {
            "action": "hello", "args": {"word": "žluťoučký"}}
        with pytest.raises(AppError) as exc:
            invoke(app_paths, "echo", {"big": "X"*(1024*1024)})
        assert exc.value.code == "TOO_LARGE"
    finally:
        server.stop()
        thread.join(3)


def test_cli_init_dry_run_no_files(app_paths):
    from dictation4me.cli import main
    assert main(["init", "--dry-run", "--no-desktop"]) == 0
    assert not app_paths.config_file.exists()
    assert main(["init", "--no-desktop"]) == 0
    assert app_paths.config_file.exists()
    assert main(["config", "validate"]) == 0


def test_service_cli_end_to_end_process(app_paths):
    from dictation4me.cli import ensure_service
    from dictation4me.ipc import invoke
    env = os.environ.copy()
    env["DICTATION4ME_HOME"] = str(app_paths.config.parent)
    try:
        status = ensure_service(app_paths, timeout=5)
        assert status["state"] in ("IDLE", "CANCELLED")
        n = invoke(app_paths, "notes.create", {"title": "CLI integration", "content": "hello"})
        assert invoke(app_paths, "notes.get", {"id": n["id"]})["content_md"] == "hello"
        result = invoke(app_paths, "notes.draft", {"id": n["id"], "text": "edited", "base_edit_seq": 0})
        assert result["edit_seq"] == 1
        assert invoke(app_paths, "integrity")["database"] == "ok"
    finally:
        try:
            invoke(app_paths, "service.stop")
        except AppError:
            pass
        time.sleep(.25)
