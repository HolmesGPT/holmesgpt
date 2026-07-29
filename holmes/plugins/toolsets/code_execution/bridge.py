"""Parent-side tool-call bridge for code mode.

While the LLM-authored script runs in a subprocess, this bridge listens on a
unix-domain socket and services each ``holmes.<tool>(...)`` request by invoking
the real tool through the ToolExecutor (in the parent, where credentials live)
and streaming the result back. It is single-connection and single-threaded: a
script's tool calls are sequential, so no concurrency is needed within one run.

The subprocess writes its stdout/stderr to a file (not a pipe), so there is no
pipe-buffer deadlock; the bridge only ever blocks on the socket, always with a
timeout, and gives up when the subprocess exits or the wall-clock deadline
passes.
"""

import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Callable, List

logger = logging.getLogger(__name__)

# {"status": "ok"|"error", "data": <str|None>, "error": <str|None>}
DispatchFn = Callable[[str, dict], dict]

_ACCEPT_TIMEOUT = 0.2
_RECV_TIMEOUT = 0.2


@dataclass
class SubToolCall:
    """A record of one tool call made from inside a code-mode script."""

    tool_name: str
    params: dict
    status: str
    error: str = ""
    output_chars: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class ToolCallBridge:
    dispatch: DispatchFn
    socket_path: str
    records: List[SubToolCall] = field(default_factory=list)
    _server: socket.socket = field(default=None, init=False, repr=False)

    def __enter__(self) -> "ToolCallBridge":
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.socket_path)
        self._server.listen(1)
        self._server.settimeout(_ACCEPT_TIMEOUT)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._server is not None:
                self._server.close()
        finally:
            if os.path.exists(self.socket_path):
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass

    def _handle_line(self, line: bytes) -> dict:
        try:
            req = json.loads(line.decode())
        except Exception:
            return {"status": "error", "error": "malformed tool-call request"}
        tool_name = req.get("tool")
        params = req.get("params") or {}
        if not isinstance(tool_name, str) or not isinstance(params, dict):
            return {"status": "error", "error": "invalid tool-call request"}
        started = time.monotonic()
        try:
            resp = self.dispatch(tool_name, params)
        except Exception as exc:  # dispatch must not crash the bridge
            logger.warning("code-mode dispatch of %s raised", tool_name, exc_info=True)
            resp = {"status": "error", "error": f"tool dispatch failed: {exc}"}
        self.records.append(
            SubToolCall(
                tool_name=tool_name,
                params=params,
                status=resp.get("status", "error"),
                error=resp.get("error") or "",
                output_chars=len(resp.get("data") or ""),
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
        )
        return resp

    def serve_until_exit(self, process, deadline: float) -> None:
        """Service requests until the subprocess exits or ``deadline`` passes."""
        conn = None
        buf = b""
        try:
            while True:
                if conn is None:
                    if process.poll() is not None:
                        return
                    if time.monotonic() > deadline:
                        return
                    try:
                        conn, _ = self._server.accept()
                        conn.settimeout(_RECV_TIMEOUT)
                        buf = b""
                    except socket.timeout:
                        continue
                    continue

                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    if process.poll() is not None or time.monotonic() > deadline:
                        return
                    continue
                if not chunk:
                    conn.close()
                    conn = None
                    if process.poll() is not None:
                        return
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    resp = self._handle_line(line)
                    try:
                        conn.sendall((json.dumps(resp) + "\n").encode())
                    except OSError:
                        return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
