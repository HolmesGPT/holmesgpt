"""Subprocess bootstrap for the code_execution toolset ("code mode").

This file is executed by a fresh ``python3`` process (NOT inside the Holmes
process). It uses ONLY the standard library so it runs anywhere ``python3``
exists. It:

  1. builds a ``holmes`` namespace whose attributes are functions, one per
     tool that the parent Holmes process made available;
  2. runs the LLM-authored user script with ``holmes`` in scope;
  3. relays every ``holmes.<tool>(...)`` call to the parent over a unix-domain
     socket and returns the tool output to the script.

Credentials and the real ToolExecutor live in the PARENT process only; this
subprocess can do nothing but ask the parent to run an allow-listed tool.

Environment (set by the parent):
  HOLMES_CODE_SOCKET     path to the parent's AF_UNIX socket
  HOLMES_CODE_USER_FILE  path to the file containing the user's Python script
  HOLMES_CODE_TOOLS      path to a JSON file: [{"attr": ..., "name": ...}, ...]
"""

import json
import os
import socket
import sys
import traceback
import types


class HolmesToolError(Exception):
    """Raised inside a code-mode script when a tool call fails."""


class _Bridge:
    """Newline-delimited JSON request/response over a persistent unix socket."""

    def __init__(self, path: str):
        self._path = path
        self._conn = None
        self._buf = b""

    def _connect(self):
        if self._conn is None:
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.connect(self._path)
            self._conn = conn
        return self._conn

    def _read_line(self) -> bytes:
        while b"\n" not in self._buf:
            chunk = self._conn.recv(65536)
            if not chunk:
                break
            self._buf += chunk
        if b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            return line
        line, self._buf = self._buf, b""
        return line

    def call(self, tool_name: str, params: dict):
        conn = self._connect()
        conn.sendall((json.dumps({"tool": tool_name, "params": params}) + "\n").encode())
        line = self._read_line()
        if not line:
            raise HolmesToolError(f"no response from Holmes for tool '{tool_name}'")
        resp = json.loads(line.decode())
        if resp.get("status") == "error":
            raise HolmesToolError(resp.get("error") or f"tool '{tool_name}' failed")
        return resp.get("data")


def _build_holmes_namespace(bridge: _Bridge, tools: list) -> types.SimpleNamespace:
    ns = types.SimpleNamespace()
    ns.HolmesToolError = HolmesToolError

    def _make(real_name: str):
        def _fn(**params):
            return bridge.call(real_name, params)

        return _fn

    for spec in tools:
        setattr(ns, spec["attr"], _make(spec["name"]))
    return ns


def main() -> int:
    socket_path = os.environ["HOLMES_CODE_SOCKET"]
    user_file = os.environ["HOLMES_CODE_USER_FILE"]
    tools_file = os.environ["HOLMES_CODE_TOOLS"]

    with open(tools_file) as fh:
        tools = json.load(fh)
    with open(user_file) as fh:
        source = fh.read()

    bridge = _Bridge(socket_path)
    holmes = _build_holmes_namespace(bridge, tools)

    user_globals = {
        "__name__": "__main__",
        "holmes": holmes,
        "HolmesToolError": HolmesToolError,
    }

    try:
        code = compile(source, "<holmes-code>", "exec")
    except SyntaxError:
        traceback.print_exc()
        return 2
    try:
        exec(code, user_globals)  # noqa: S102 - executing LLM-authored code is the point
    except HolmesToolError as exc:
        print(f"\n[holmes] tool call failed: {exc}", file=sys.stderr)
        return 3
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
