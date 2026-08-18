import ipaddress
import socket
import threading

import pytest

from holmes.core.tools import ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.connectivity_check import (
    ConnectivityCheckToolset,
    tcp_check,
)
from holmes.plugins.toolsets.internet import ssrf
from holmes.plugins.toolsets.internet.ssrf import is_blocked_ip
from tests.conftest import create_mock_tool_invoke_context


def start_tcp_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    port = server_socket.getsockname()[1]
    stop_event = threading.Event()

    def serve():
        while not stop_event.is_set():
            try:
                server_socket.settimeout(0.1)
                conn, _ = server_socket.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server_socket, port, stop_event, thread


def get_unused_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_tcp_check_success():
    server_socket, port, stop_event, thread = start_tcp_server()
    try:
        result = tcp_check("127.0.0.1", port, timeout=1)
        assert result["ok"] is True
    finally:
        stop_event.set()
        server_socket.close()
        thread.join(timeout=1)


def test_tcp_check_invalid_port():
    result = tcp_check("127.0.0.1", 70000, timeout=3.0)
    assert result["ok"] is False
    assert "invalid port" in result["error"]


def test_tcp_check_unreachable_port():
    port = get_unused_port()
    result = tcp_check("127.0.0.1", port, timeout=1)
    assert result["ok"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# SSRF guard (ROB-896 follow-up): tcp_check's host is model-controlled.
# ---------------------------------------------------------------------------


def _build_tool(config=None):
    toolset = ConnectivityCheckToolset()
    ok, err = toolset.prerequisites_callable(config or {})
    assert ok, f"Setup failed: {err}"
    toolset.status = ToolsetStatusEnum.ENABLED
    tool = ToolExecutor(toolsets=[toolset]).get_tool_by_name("tcp_check")
    assert tool
    return tool


def test_is_blocked_ip_allows_private_when_opted_in():
    # Private ranges are reachable when allow_private_ips is set...
    for ip in ["10.0.0.1", "192.168.1.1", "172.16.0.1"]:
        assert not is_blocked_ip(ipaddress.ip_address(ip), allow_private_ips=True), ip
    # ...but metadata/loopback/link-local stay blocked regardless.
    for ip in ["169.254.169.254", "127.0.0.1", "::1", "fe80::1", "224.0.0.1"]:
        assert is_blocked_ip(ipaddress.ip_address(ip), allow_private_ips=True), ip


@pytest.mark.parametrize(
    "host",
    ["169.254.169.254", "127.0.0.1", "::1", "0.0.0.0", "224.0.0.1"],
)
def test_tcp_check_refuses_metadata_and_local_targets(host):
    tool = _build_tool()
    result = tool.invoke(
        {"host": host, "port": 80}, create_mock_tool_invoke_context()
    )
    assert result.data["ok"] is False
    assert "Refusing to connect" in result.data["error"]


def test_tcp_check_allows_private_cluster_ip_by_default(monkeypatch):
    """Private/cluster IPs are the tool's legitimate purpose — they must not be
    refused (they may still fail to connect, but not be blocked by the guard)."""
    tool = _build_tool()
    result = tool.invoke(
        {"host": "10.255.255.1", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    # Not refused by the SSRF guard (it either connects or fails to connect).
    assert "Refusing to connect" not in str(result.data)


def test_tcp_check_block_private_ips_config(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    tool = _build_tool({"block_private_ips": True})
    result = tool.invoke(
        {"host": "internal.svc", "port": 8080}, create_mock_tool_invoke_context()
    )
    assert result.data["ok"] is False
    assert "Refusing to connect" in result.data["error"]


def test_tcp_check_connects_to_validated_ip(monkeypatch):
    """The probe connects to the validated IP (defeating DNS rebinding) and an
    allowlisted host bypasses the internal-IP block."""
    server_socket, port, stop_event, thread = start_tcp_server()

    def fake_getaddrinfo(host, p, *args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", p))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    try:
        # Allowlist exempts the (loopback) target from the block; the connection
        # must go to the validated 127.0.0.1 where our server is listening.
        tool = _build_tool({"allowed_hosts": ["db.internal.test"]})
        result = tool.invoke(
            {"host": "db.internal.test", "port": port, "timeout": 1},
            create_mock_tool_invoke_context(),
        )
        assert result.data["ok"] is True
    finally:
        stop_event.set()
        server_socket.close()
        thread.join(timeout=1)
