"""SSRF regression tests for the internet / notion toolsets.

Covers ROB-896: fetch_webpage must not be usable as a Server-Side Request
Forgery / data-exfiltration primitive (cloud metadata, loopback, RFC1918,
link-local), must restrict schemes, must honour an optional allowlist, must not
leak operator auth headers to arbitrary/redirected hosts, and must pin the
connection to the validated IP to defeat DNS rebinding.
"""

import http.server
import ipaddress
import socketserver
import threading

import pytest
import requests

from holmes.core.tools import ToolsetStatusEnum
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.internet import ssrf
from holmes.plugins.toolsets.internet.internet import InternetToolset, scrape
from holmes.plugins.toolsets.internet.ssrf import (
    SSRFValidationError,
    build_pinned_adapter,
    is_blocked_ip,
    validate_url,
)
from tests.conftest import create_mock_tool_invoke_context

# IP literals are validated without any DNS lookup, so these stay hermetic.
BLOCKED_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP metadata
    "http://169.254.169.254/",
    "http://[fd00:ec2::254]/latest/meta-data/",  # IMDSv6 (unique-local)
    "http://127.0.0.1/",
    "http://127.0.0.1:8080/admin",
    "http://[::1]/",
    "http://10.0.0.1/",
    "http://10.1.2.3:9200/_cluster/health",
    "http://192.168.1.1/",
    "http://172.16.0.5/",
    "http://0.0.0.0/",
    "http://[::ffff:169.254.169.254]/",  # IPv4-mapped metadata address
]

BLOCKED_SCHEMES = [
    "file:///etc/passwd",
    "ftp://internal/secret",
    "gopher://127.0.0.1:6379/_INFO",
    "data:text/plain,hello",
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_validate_url_blocks_internal_ip_literals(url):
    with pytest.raises(SSRFValidationError):
        validate_url(url)


@pytest.mark.parametrize("url", BLOCKED_SCHEMES)
def test_validate_url_blocks_non_http_schemes(url):
    with pytest.raises(SSRFValidationError):
        validate_url(url)


def test_validate_url_blocks_hostname_resolving_to_internal(monkeypatch):
    """A hostname (not an IP literal) that resolves to an internal address is
    rejected — this is also the DNS-rebind validation path."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFValidationError):
        validate_url("http://internal.evil.example/")


def test_validate_url_allows_public_host(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_url("https://example.com/page") == ["93.184.216.34"]


def test_is_blocked_ip_ranges():
    for ip in [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.0.1",
        "172.16.0.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
        "224.0.0.1",
        "0.0.0.0",
    ]:
        assert is_blocked_ip(ipaddress.ip_address(ip)), ip
    for ip in ["8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"]:
        assert not is_blocked_ip(ipaddress.ip_address(ip)), ip


# ---------------------------------------------------------------------------
# scrape() level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_scrape_refuses_internal_ip_and_makes_no_request(url, responses):
    content, mime = scrape(url, {})
    assert mime is None
    assert content is not None and content.startswith("Refusing to fetch")
    # The guard must reject before any HTTP request is made.
    assert len(responses.calls) == 0


def test_scrape_refuses_disallowed_scheme(responses):
    content, mime = scrape("file:///etc/passwd", {})
    assert mime is None
    assert content.startswith("Refusing to fetch")
    assert len(responses.calls) == 0


def test_scrape_block_can_be_disabled(monkeypatch, responses):
    """block_internal_ips=False is an explicit opt-out for isolated envs."""
    responses.get("http://10.0.0.1/health", status=200, body="ok")
    content, _ = scrape("http://10.0.0.1/health", {}, block_internal_ips=False)
    assert content == "ok"
    assert len(responses.calls) == 1


def test_scrape_allowlist_permits_internal_host(monkeypatch, responses):
    """An operator allowlist opts a host in, exempting it from the IP block."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("10.0.0.9", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    responses.get("http://wiki.internal.corp/page", status=200, body="secret-wiki")
    content, _ = scrape(
        "http://wiki.internal.corp/page",
        {},
        allowed_hosts=["wiki.internal.corp"],
    )
    assert content == "secret-wiki"


def test_scrape_allowlist_rejects_other_hosts(monkeypatch, responses):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    content, mime = scrape(
        "https://not-allowed.example/", {}, allowed_hosts=["wiki.internal.corp"]
    )
    assert mime is None
    assert content.startswith("Refusing to fetch")
    assert len(responses.calls) == 0


def test_scrape_strips_auth_on_cross_host_redirect(monkeypatch, responses):
    """Credential headers must not follow a redirect to a different host."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    responses.get(
        "https://trusted.example/start",
        status=302,
        headers={"Location": "https://evil.example/collect"},
    )
    responses.get("https://evil.example/collect", status=200, body="landed")

    content, _ = scrape(
        "https://trusted.example/start",
        {"Authorization": "Bearer super-secret"},
        allowed_hosts=["trusted.example", "evil.example"],
    )
    assert content == "landed"
    assert len(responses.calls) == 2
    # First hop keeps the auth header; second (cross-host) hop must not.
    assert responses.calls[0].request.headers.get("Authorization") == "Bearer super-secret"
    assert "Authorization" not in responses.calls[1].request.headers


def test_scrape_redirect_to_internal_is_blocked(monkeypatch, responses):
    """A redirect that points at an internal address is re-validated and blocked."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    responses.get(
        "https://public.example/redir",
        status=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )
    content, mime = scrape("https://public.example/redir", {})
    assert mime is None
    assert content.startswith("Refusing to fetch")
    # Only the first hop was requested; the redirect target was blocked.
    assert len(responses.calls) == 1


# ---------------------------------------------------------------------------
# Tool level
# ---------------------------------------------------------------------------


def _build_tool(config=None):
    toolset = InternetToolset()
    success, error = toolset.prerequisites_callable(config or {})
    assert success, f"Setup failed: {error}"
    toolset.status = ToolsetStatusEnum.ENABLED
    tool = ToolExecutor(toolsets=[toolset]).get_tool_by_name("fetch_webpage")
    assert tool
    return tool


def test_fetch_webpage_tool_rejects_metadata_endpoint(responses):
    tool = _build_tool()
    result = tool.invoke(
        {"url": "http://169.254.169.254/latest/meta-data/"},
        create_mock_tool_invoke_context(),
    )
    assert len(responses.calls) == 0
    text = f"{result.data or ''}{result.error or ''}"
    assert "Refusing to fetch" in text


def test_fetch_webpage_tool_does_not_send_auth_without_allowlist(
    monkeypatch, responses
):
    """Without an allowlist, operator auth headers must not be forwarded to the
    model-chosen host."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    responses.get("https://attacker.example/", status=200, body="ok")

    tool = _build_tool(
        {"additional_headers": {"Authorization": "Bearer super-secret"}}
    )
    result = tool.invoke(
        {"url": "https://attacker.example/"}, create_mock_tool_invoke_context()
    )
    assert result.data == "ok"
    assert len(responses.calls) == 1
    assert "Authorization" not in responses.calls[0].request.headers


# ---------------------------------------------------------------------------
# DNS-rebind pinning mechanism
# ---------------------------------------------------------------------------


def test_pinned_adapter_connects_to_validated_ip(responses):
    """The pinned adapter connects to the validated IP regardless of what the
    URL hostname would resolve to — this is what defeats DNS rebinding. The
    Host header (and thus TLS SNI/cert) still uses the real hostname."""
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received["host"] = self.headers.get("Host")
            received["peer"] = self.connection.getpeername()[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pinned-ok")

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # Let the real socket through (responses would otherwise intercept it).
        responses.add_passthru("http://internal.example.test")
        session = requests.Session()
        adapter = build_pinned_adapter("127.0.0.1")
        session.mount("http://", adapter)
        resp = session.get(f"http://internal.example.test:{port}/x", timeout=5)
        assert resp.status_code == 200
        assert resp.text == "pinned-ok"
        # Connected to the pinned IP, but Host header preserved the hostname.
        assert received["peer"] == "127.0.0.1"
        assert received["host"] == f"internal.example.test:{port}"
    finally:
        server.shutdown()
        server.server_close()
