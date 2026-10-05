import logging
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import server

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_importing_server_emits_no_on_event_deprecation_warning():
    # A fresh interpreter, because the warning fires once at import time and
    # this module's own `import server` has already run.
    result = subprocess.run(
        [sys.executable, "-W", "always::DeprecationWarning", "-c", "import server"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "on_event is deprecated" not in result.stderr


def test_app_registers_no_legacy_event_handlers():
    assert server.app.router.on_startup == []
    assert server.app.router.on_shutdown == []


def test_worker_is_stopped_on_shutdown_not_on_startup():
    worker = MagicMock()
    with patch.object(server, "conversation_worker", worker):
        with TestClient(server.app) as client:
            assert client.get("/healthz").status_code == 200
            worker.stop.assert_not_called()
        worker.stop.assert_called_once_with()
    worker.start.assert_not_called()


def test_shutdown_without_worker_is_a_noop():
    with patch.object(server, "conversation_worker", None):
        with TestClient(server.app) as client:
            assert client.get("/healthz").status_code == 200


def test_failing_stop_is_logged_and_does_not_break_shutdown(caplog):
    worker = MagicMock()
    worker.stop.side_effect = RuntimeError("supabase down")
    with patch.object(server, "conversation_worker", worker):
        with caplog.at_level(logging.ERROR):
            with TestClient(server.app):
                pass
    worker.stop.assert_called_once_with()
    assert "Failed to stop conversation worker" in caplog.text
    assert "supabase down" in caplog.text


def test_stop_runs_off_the_event_loop_thread():
    loop_threads = []
    stop_threads = []

    async def record_loop_thread():
        loop_threads.append(threading.current_thread())
        return {"ok": True}

    worker = MagicMock()
    worker.stop.side_effect = lambda: stop_threads.append(threading.current_thread())

    server.app.add_api_route("/__test_loop_thread", record_loop_thread)
    try:
        with patch.object(server, "conversation_worker", worker):
            with TestClient(server.app) as client:
                client.get("/__test_loop_thread")
    finally:
        server.app.router.routes = [
            r
            for r in server.app.router.routes
            if getattr(r, "path", None) != "/__test_loop_thread"
        ]

    assert len(stop_threads) == 1
    assert stop_threads[0] is not loop_threads[0]


def test_each_lifespan_cycle_stops_the_worker_once():
    worker = MagicMock()
    with patch.object(server, "conversation_worker", worker):
        for _ in range(2):
            with TestClient(server.app):
                pass
    assert worker.stop.call_count == 2
