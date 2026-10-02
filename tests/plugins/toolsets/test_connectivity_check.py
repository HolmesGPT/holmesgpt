import ipaddress
import logging
import socket
import threading

import pytest

from holmes.core.tools import ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets import connectivity_check
from holmes.plugins.toolsets.connectivity_check import (
    PROBE_AUDIT_PREFIX,
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


def _private_dns(monkeypatch, ip="10.0.0.5"):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)


def _stub_probe(monkeypatch):
    """Replace the socket probe and record what it was asked to connect to.

    Tests that assert a probe was *authorized* must not reach the network: a
    transport error to an unroutable test address looks identical to a policy
    refusal if you only assert on the absence of refusal text. Recording the
    call makes "the policy let this through, and to this address" observable.
    """
    calls = []

    def fake_tcp_check(host, port, timeout):
        calls.append((host, port, timeout))
        return {"ok": True}

    monkeypatch.setattr(connectivity_check, "tcp_check", fake_tcp_check)
    return calls


# ---------------------------------------------------------------------------
# ROB-1114: private destinations require an explicit allowlist
#
# tcp_check returns distinguishable open / refused / filtered outcomes, so
# leaving RFC1918 blanket-reachable made it a blind internal port scanner
# driven by whatever the model reads. Probing named internal services is the
# tool's purpose, so the fix is to require the operator to name them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["10.255.255.1", "192.168.1.10", "172.16.0.5", "10.0.0.1"]
)
def test_tcp_check_refuses_private_target_without_allowlist(host):
    """With defaults, an arbitrary RFC1918 address is refused."""
    tool = _build_tool()
    result = tool.invoke(
        {"host": host, "port": 9, "timeout": 0.1}, create_mock_tool_invoke_context()
    )
    assert result.data["ok"] is False
    assert "Refusing to connect" in result.data["error"]
    assert "allowed_hosts" in result.data["error"]


def test_private_refusal_is_indistinguishable_across_targets():
    """The refusal must not leak whether a port is open, closed or filtered —
    that differentiation is the enumeration oracle."""
    tool = _build_tool()
    errors = set()
    for host, port in [
        ("10.0.0.1", 22),
        ("10.0.0.1", 9),
        ("192.168.50.7", 443),
    ]:
        result = tool.invoke(
            {"host": host, "port": port, "timeout": 0.1},
            create_mock_tool_invoke_context(),
        )
        # Normalise away the host:port echo; what matters is the reason.
        errors.add(result.data["error"].split(": ", 1)[1].split("'")[0])
    assert len(errors) == 1, errors


def test_refusal_does_not_leak_the_resolved_internal_address(monkeypatch):
    """The refusal must not answer 'what does this internal name resolve to?'.
    Echoing the resolved IP would turn a blocked probe into a DNS-mapping
    oracle; the address belongs in the audit log, not the model's context."""
    _private_dns(monkeypatch, "10.11.12.13")
    tool = _build_tool()
    result = tool.invoke(
        {"host": "billing.internal", "port": 8080, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "10.11.12.13" not in result.data["error"]
    assert "allowed_hosts" in result.data["error"]


def test_metadata_refusal_does_not_leak_the_resolved_address(monkeypatch):
    """Same rule on the metadata/loopback path: a hostname that resolves into a
    blocked range must not have that address handed back."""
    _private_dns(monkeypatch, "169.254.169.254")
    tool = _build_tool()
    result = tool.invoke(
        {"host": "metadata.internal", "port": 80, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "169.254.169.254" not in result.data["error"]
    assert "non-routable/internal address" in result.data["error"]


def test_tcp_check_allows_private_target_named_by_hostname(monkeypatch):
    """An allowlisted internal service still succeeds."""
    server_socket, port, stop_event, thread = start_tcp_server()

    def fake_getaddrinfo(host, p, *args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", p))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    try:
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


def test_tcp_check_allows_private_target_named_by_cidr(monkeypatch):
    """CIDR entries let an operator name a whole internal range (e.g. the
    cluster service network) without listing every host."""
    _private_dns(monkeypatch, "10.96.1.20")
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"allowed_hosts": ["10.96.0.0/12"]})
    result = tool.invoke(
        {"host": "svc.cluster.local", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True
    assert probes == [("10.96.1.20", 9, 0.1)]


def test_cidr_allowlist_does_not_cover_other_private_ranges(monkeypatch):
    """A CIDR entry must not act as a blanket private-range pass."""
    _private_dns(monkeypatch, "192.168.4.4")
    tool = _build_tool({"allowed_hosts": ["10.96.0.0/12"]})
    result = tool.invoke(
        {"host": "elsewhere.internal", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "'allowed_hosts' allowlist" in result.data["error"]


def test_allowlist_is_exhaustive_for_public_hosts_too():
    """Setting an allowlist restricts every destination, not just private ones."""
    tool = _build_tool({"allowed_hosts": ["allowed.example.com"]})
    result = tool.invoke(
        {"host": "example.com", "port": 443, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "'allowed_hosts' allowlist" in result.data["error"]
    # The refusal must not echo the allowlist itself — those are the operator's
    # internal service names, and handing them to the model is free recon.
    assert "allowed.example.com" not in result.data["error"]


def test_public_destination_still_allowed_by_default(monkeypatch):
    """No allowlist configured leaves public destinations reachable — the fix
    targets the internal network, not ordinary connectivity checks."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    probes = _stub_probe(monkeypatch)
    tool = _build_tool()
    result = tool.invoke(
        {"host": "example.com", "port": 443, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True
    assert probes == [("93.184.216.34", 443, 0.1)]


def test_block_internal_ips_false_restores_unrestricted_probing(monkeypatch):
    """The documented escape hatch for trusted isolated environments."""
    _private_dns(monkeypatch, "10.0.0.5")
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"block_internal_ips": False})
    result = tool.invoke(
        {"host": "internal.svc", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True
    assert probes == [("10.0.0.5", 9, 0.1)]


# ---------------------------------------------------------------------------
# allow_all_hosts: the opt-out for deployments that don't want to build an
# allowlist. It waives the *allowlist requirement* only — cloud metadata and
# loopback stay blocked, and block_private_ips still wins.
# ---------------------------------------------------------------------------


def test_allow_all_hosts_permits_private_target_without_allowlist(monkeypatch):
    _private_dns(monkeypatch, "10.0.0.5")
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"allow_all_hosts": True})
    result = tool.invoke(
        {"host": "internal.svc", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True
    assert probes == [("10.0.0.5", 9, 0.1)]


def test_allow_all_hosts_can_be_set_by_env_var(monkeypatch):
    """The point of the env var is that no config file has to change."""
    _private_dns(monkeypatch, "10.0.0.5")
    probes = _stub_probe(monkeypatch)
    monkeypatch.setenv(connectivity_check.ALLOW_ALL_HOSTS_ENV_VAR, "true")
    tool = _build_tool()
    result = tool.invoke(
        {"host": "internal.svc", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True
    assert probes == [("10.0.0.5", 9, 0.1)]


@pytest.mark.parametrize("value", ["", "false", "0", "no", "maybe"])
def test_allow_all_hosts_env_var_fails_safe(monkeypatch, value):
    """Anything but an explicit truthy value leaves the guard on."""
    monkeypatch.setenv(connectivity_check.ALLOW_ALL_HOSTS_ENV_VAR, value)
    tool = _build_tool()
    result = tool.invoke(
        {"host": "10.0.0.1", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "allowed_hosts" in result.data["error"]


def test_allow_all_hosts_still_blocks_metadata_and_loopback(monkeypatch):
    """The escape hatch is for the operator's own network, not for the cloud
    credential endpoint — that stays behind block_internal_ips."""
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"allow_all_hosts": True})
    for host in ["169.254.169.254", "127.0.0.1", "::1"]:
        result = tool.invoke(
            {"host": host, "port": 80, "timeout": 0.1},
            create_mock_tool_invoke_context(),
        )
        assert result.data["ok"] is False, host
        assert "Refusing to connect" in result.data["error"], host
    assert probes == []


def test_allow_all_hosts_does_not_beat_block_private_ips(monkeypatch):
    """block_private_ips is the 'public hosts only' switch and stays absolute."""
    _private_dns(monkeypatch, "10.0.0.5")
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"allow_all_hosts": True, "block_private_ips": True})
    result = tool.invoke(
        {"host": "internal.svc", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "block_private_ips is set" in result.data["error"]
    assert probes == []


@pytest.mark.parametrize(
    "entry,host",
    [
        ("169.254.0.0/16", "169.254.169.254"),
        ("169.254.169.254", "169.254.169.254"),
        ("127.0.0.0/8", "127.0.0.1"),
        ("::1", "::1"),
        ("fe80::/10", "fe80::1"),
    ],
)
def test_allow_all_hosts_ignores_an_allowlist_exemption_for_protected_ranges(
    monkeypatch, entry, host
):
    """An allowlist entry normally exempts its destination from the
    metadata/loopback block. That exemption must not survive allow_all_hosts —
    otherwise a '169.254.0.0/16' entry quietly reopens the metadata endpoint,
    which is exactly what allow_all_hosts promises never to do."""
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"allow_all_hosts": True, "allowed_hosts": [entry]})
    result = tool.invoke(
        {"host": host, "port": 80, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "Refusing to connect" in result.data["error"]
    assert probes == []


@pytest.mark.parametrize(
    "config",
    [
        {"block_internal_ips": False},
        {"block_internal_ips": False, "allow_all_hosts": True},
    ],
)
def test_allow_all_hosts_neither_causes_nor_countermands_block_internal_ips(
    monkeypatch, config
):
    """`block_internal_ips: false` is the documented 'no range checks' switch and
    is what makes metadata reachable — with or without allow_all_hosts, as these
    two cases show. allow_all_hosts is therefore not the cause, and it must not
    silently re-impose a block the operator explicitly turned off: a permissive
    flag that quietly narrows another setting is its own kind of surprise."""
    probes = _stub_probe(monkeypatch)
    tool = _build_tool(config)
    result = tool.invoke(
        {"host": "169.254.169.254", "port": 80, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True, config
    assert probes == [("169.254.169.254", 80, 0.1)], config


def test_allowlist_exemption_still_works_without_allow_all_hosts(monkeypatch):
    """The counterpart: with allow_all_hosts off, deliberately naming a
    protected address still works — that's the documented way to target one."""
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"allowed_hosts": ["169.254.169.254"]})
    result = tool.invoke(
        {"host": "169.254.169.254", "port": 80, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True
    assert probes == [("169.254.169.254", 80, 0.1)]


def test_allow_all_hosts_overrides_a_configured_allowlist(monkeypatch):
    """Setting both is contradictory; allow_all_hosts wins (and is warned about
    at startup) rather than the allowlist silently half-applying."""
    _private_dns(monkeypatch, "192.168.4.4")
    probes = _stub_probe(monkeypatch)
    tool = _build_tool(
        {"allow_all_hosts": True, "allowed_hosts": ["10.96.0.0/12"]}
    )
    result = tool.invoke(
        {"host": "elsewhere.internal", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is True
    assert probes == [("192.168.4.4", 9, 0.1)]


def test_allow_all_hosts_warns_at_startup(caplog):
    """Avi's requirement: if you turn the guard off, it says so out loud."""
    toolset = ConnectivityCheckToolset()
    with caplog.at_level(logging.WARNING):
        ok, _ = toolset.prerequisites_callable({"allow_all_hosts": True})
    assert ok
    assert any(
        "allow_all_hosts is ON" in r.message for r in caplog.records
    ), caplog.text

    # No allowlist configured, so no override warning; with one, both fire.
    caplog.clear()
    toolset = ConnectivityCheckToolset()
    with caplog.at_level(logging.WARNING):
        toolset.prerequisites_callable(
            {"allow_all_hosts": True, "allowed_hosts": ["10.96.0.0/12"]}
        )
    assert any("overrides it" in r.message for r in caplog.records), caplog.text


def test_no_warning_when_allow_all_hosts_is_off(caplog):
    toolset = ConnectivityCheckToolset()
    with caplog.at_level(logging.WARNING):
        toolset.prerequisites_callable({"allowed_hosts": ["10.96.0.0/12"]})
    assert not any("allow_all_hosts" in r.message for r in caplog.records)


def test_block_private_ips_applies_when_block_internal_ips_is_off(monkeypatch):
    """block_private_ips refuses private destinations outright, so turning off
    block_internal_ips must not silently disable it too."""
    _private_dns(monkeypatch, "10.0.0.5")
    probes = _stub_probe(monkeypatch)
    tool = _build_tool({"block_internal_ips": False, "block_private_ips": True})
    result = tool.invoke(
        {"host": "internal.svc", "port": 9, "timeout": 0.1},
        create_mock_tool_invoke_context(),
    )
    assert result.data["ok"] is False
    assert "block_private_ips is set" in result.data["error"]
    assert probes == []


def test_block_private_ips_overrides_allowlist(monkeypatch):
    """block_private_ips is the strictest setting: private is refused even when
    the operator named the destination."""
    _private_dns(monkeypatch, "10.0.0.5")
    tool = _build_tool(
        {"block_private_ips": True, "allowed_hosts": ["internal.svc"]}
    )
    result = tool.invoke(
        {"host": "internal.svc", "port": 8080}, create_mock_tool_invoke_context()
    )
    assert result.data["ok"] is False
    assert "block_private_ips is set" in result.data["error"]


def test_probe_rate_limit_bounds_sweeping(monkeypatch):
    """A wide allowlist must not be sweepable without limit."""
    _private_dns(monkeypatch, "10.96.1.20")
    _stub_probe(monkeypatch)
    tool = _build_tool({"allowed_hosts": ["10.96.0.0/12"], "max_probes": 3})
    outcomes = []
    for _ in range(5):
        result = tool.invoke(
            {"host": "svc.cluster.local", "port": 9, "timeout": 0.05},
            create_mock_tool_invoke_context(),
        )
        outcomes.append("rate limit" in str(result.data.get("error", "")))
    assert outcomes == [False, False, False, True, True]


def test_probe_rate_limit_window_expiry_restores_capacity(monkeypatch):
    """The limit is a sliding window, not a lifetime cap: once the window has
    passed, legitimate use gets its budget back."""
    _private_dns(monkeypatch, "10.96.1.20")
    probes = _stub_probe(monkeypatch)
    tool = _build_tool(
        {
            "allowed_hosts": ["10.96.0.0/12"],
            "max_probes": 2,
            "probe_window_seconds": 30,
        }
    )

    clock = [1000.0]
    monkeypatch.setattr(connectivity_check.time, "monotonic", lambda: clock[0])

    def probe():
        result = tool.invoke(
            {"host": "svc.cluster.local", "port": 9, "timeout": 0.05},
            create_mock_tool_invoke_context(),
        )
        return "rate limit" in str(result.data.get("error", ""))

    assert [probe(), probe(), probe()] == [False, False, True]

    # Still inside the window: no capacity yet.
    clock[0] += 29
    assert probe() is True

    # Window has now passed for the first two probes.
    clock[0] += 2
    assert [probe(), probe(), probe()] == [False, False, True]
    assert len(probes) == 4


def test_probe_rate_limit_can_be_disabled(monkeypatch):
    _private_dns(monkeypatch, "10.96.1.20")
    _stub_probe(monkeypatch)
    tool = _build_tool({"allowed_hosts": ["10.96.0.0/12"], "max_probes": 0})
    for _ in range(5):
        result = tool.invoke(
            {"host": "svc.cluster.local", "port": 9, "timeout": 0.05},
            create_mock_tool_invoke_context(),
        )
        assert "rate limit" not in str(result.data.get("error", ""))


@pytest.mark.parametrize(
    "config",
    [
        {"probe_window_seconds": 0},
        {"probe_window_seconds": -1},
        {"max_probes": -1},
    ],
)
def test_nonpositive_rate_limit_settings_are_rejected(config):
    """A zero-length window (or a negative cap) would evict every entry on the
    next call and silently disable the limit — fail loudly at config time
    instead of pretending to rate limit."""
    toolset = ConnectivityCheckToolset()
    ok, err = toolset.prerequisites_callable(config)
    assert ok is False
    assert "Invalid connectivity_check configuration" in err


def test_every_probe_attempt_is_audit_logged(monkeypatch, caplog):
    """Allowed and refused probes are both recorded, so scanning is visible."""
    _private_dns(monkeypatch, "10.96.1.20")
    _stub_probe(monkeypatch)
    tool = _build_tool({"allowed_hosts": ["10.96.0.0/12"]})
    with caplog.at_level(logging.INFO):
        tool.invoke(
            {"host": "svc.cluster.local", "port": 9, "timeout": 0.05},
            create_mock_tool_invoke_context(),
        )
    assert any(
        PROBE_AUDIT_PREFIX in r.message and "ALLOWED" in r.message
        for r in caplog.records
    ), caplog.text

    refused_tool = _build_tool()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        refused_tool.invoke(
            {"host": "10.0.0.1", "port": 9, "timeout": 0.05},
            create_mock_tool_invoke_context(),
        )
    assert any(
        PROBE_AUDIT_PREFIX in r.message and "REFUSED" in r.message
        for r in caplog.records
    ), caplog.text


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
