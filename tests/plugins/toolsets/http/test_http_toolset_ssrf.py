"""SSRF regression tests for the http toolset.

Covers ROB-915: the endpoint whitelist (`match_endpoint`) was enforced only
against the ORIGINAL url, while `requests` followed 30x responses by default.
Any whitelisted host that could be made to emit a redirect (open redirect,
attacker-controlled path/param, compromised upstream) therefore pivoted the
request to cloud metadata / in-cluster services, and operator-configured auth
was replayed to the redirect target.

Every hop must now be re-validated against the whitelist, credentials must be
dropped when a redirect crosses an origin, and the chain must be bounded.
"""

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.http import http_toolset
from holmes.plugins.toolsets.http.http_toolset import (
    MAX_REDIRECTS,
    AuthConfig,
    EndpointConfig,
    HttpRequest,
    HttpToolset,
    HttpToolsetConfig,
)
from holmes.plugins.toolsets.internet import ssrf
from tests.conftest import create_mock_tool_invoke_context

# Targets an attacker would pivot to via an unvalidated redirect.
INTERNAL_REDIRECT_TARGETS = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP metadata
    "http://169.254.169.254/computeMetadata/v1/",
    "http://127.0.0.1:8080/admin",
    "http://localhost:6379/",
    "http://10.1.2.3:9200/_cluster/health",
    "http://kubernetes.default.svc/api/v1/secrets",
    "http://192.168.1.1/",
]

REDIRECT_STATUSES = [301, 302, 303, 307, 308]


def build_tool(
    hosts=None,
    auth=None,
    paths=None,
    methods=None,
    extra_endpoints=None,
    **config_kwargs,
):
    """An http toolset whose whitelist contains api.example.com (and whatever
    else the test adds)."""
    endpoints = [
        EndpointConfig(
            hosts=hosts or ["api.example.com"],
            paths=paths or ["*"],
            auth=auth or AuthConfig(type="none"),
            **({"methods": methods} if methods else {}),
        )
    ]
    endpoints.extend(extra_endpoints or [])
    toolset = HttpToolset()
    toolset._http_config = HttpToolsetConfig(endpoints=endpoints, **config_kwargs)
    return HttpRequest(toolset)


def register_trap(responses, url, method="GET", **kwargs):
    """Register a mock for a target that MUST NOT be reached.

    Registering it means a regression shows up as a successful fetch that the
    assertions catch, instead of as an unmatched-mock connection error that
    could be mistaken for the guard working. The trap is expected to stay
    unfired, so the fixture's all-mocks-fired assertion is relaxed.
    """
    responses.assert_all_requests_are_fired = False
    responses.add(method, url, **kwargs)


def public_dns(monkeypatch):
    """Make every hostname resolve to a public address, so `block_internal_ips`
    tests exercise policy rather than real DNS."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)


def internal_dns(monkeypatch, ip="10.0.0.5"):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# The core vulnerability: redirect to a non-whitelisted internal target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", INTERNAL_REDIRECT_TARGETS)
def test_redirect_to_non_whitelisted_target_is_refused(target, responses):
    """A whitelisted host answering 302 -> internal target must not be followed,
    and the internal target must never be contacted."""
    responses.get(
        "https://api.example.com/open-redirect",
        status=302,
        headers={"Location": target},
    )
    register_trap(responses, target, body='{"secret": "leaked"}', status=200)

    tool = build_tool()
    result = tool._invoke(
        {"url": "https://api.example.com/open-redirect"},
        create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "Refusing to follow redirect" in result.error
    assert "leaked" not in str(result.data)
    # Only the first (whitelisted) hop was ever sent.
    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == "https://api.example.com/open-redirect"


@pytest.mark.parametrize("status", REDIRECT_STATUSES)
def test_every_redirect_status_is_re_validated(status, responses):
    responses.get(
        "https://api.example.com/r",
        status=status,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )
    register_trap(responses, "http://169.254.169.254/latest/meta-data/", body="imds")

    tool = build_tool()
    result = tool._invoke(
        {"url": "https://api.example.com/r"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "Refusing to follow redirect" in result.error
    assert len(responses.calls) == 1


def test_relative_redirect_outside_path_whitelist_is_refused(responses):
    """A relative Location is resolved against the current URL and must still
    satisfy the path whitelist."""
    responses.get(
        "https://api.example.com/v1/ok",
        status=302,
        headers={"Location": "/internal/dump"},
    )
    register_trap(responses, "https://api.example.com/internal/dump", body="dump")

    tool = build_tool(paths=["/v1/*"])
    result = tool._invoke(
        {"url": "https://api.example.com/v1/ok"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "Refusing to follow redirect" in result.error
    assert len(responses.calls) == 1


def test_redirect_inside_whitelist_is_followed(responses):
    """The fix must not break legitimate redirects that stay in the whitelist."""
    responses.get(
        "https://api.example.com/old",
        status=302,
        headers={"Location": "https://api.example.com/new"},
    )
    responses.get("https://api.example.com/new", status=200, json={"ok": True})

    tool = build_tool()
    result = tool._invoke(
        {"url": "https://api.example.com/old"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data["body"] == {"ok": True}
    assert len(responses.calls) == 2


def test_redirect_chain_is_bounded(responses):
    """A whitelisted host redirecting to itself must not loop forever."""
    responses.get(
        "https://api.example.com/loop",
        status=302,
        headers={"Location": "https://api.example.com/loop"},
    )

    tool = build_tool()
    result = tool._invoke(
        {"url": "https://api.example.com/loop"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert f"more than {MAX_REDIRECTS} redirects" in result.error
    assert len(responses.calls) == MAX_REDIRECTS + 1


def test_3xx_without_location_is_treated_as_final_response(responses):
    responses.get("https://api.example.com/x", status=304)

    tool = build_tool()
    result = tool._invoke(
        {"url": "https://api.example.com/x"}, create_mock_tool_invoke_context()
    )

    assert result.data["status_code"] == 304
    assert len(responses.calls) == 1


# ---------------------------------------------------------------------------
# Credential leakage across a redirect
# ---------------------------------------------------------------------------


def test_bearer_token_stripped_on_cross_host_redirect(responses):
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "https://other.example.com/collect"},
    )
    responses.get("https://other.example.com/collect", status=200, json={"ok": True})

    tool = build_tool(
        auth=AuthConfig(type="bearer", token="super-secret"),
        extra_endpoints=[
            EndpointConfig(hosts=["other.example.com"], auth=AuthConfig(type="none"))
        ],
    )
    result = tool._invoke(
        {"url": "https://api.example.com/go"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert len(responses.calls) == 2
    assert responses.calls[0].request.headers["Authorization"] == "Bearer super-secret"
    assert "Authorization" not in responses.calls[1].request.headers


def test_custom_auth_header_stripped_on_cross_host_redirect(responses):
    """`requests` only strips 'Authorization' on a host change; an operator
    using `auth: {type: header}` (e.g. X-API-Key) leaked the credential."""
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "https://other.example.com/collect"},
    )
    responses.get("https://other.example.com/collect", status=200, json={"ok": True})

    tool = build_tool(
        auth=AuthConfig(type="header", name="X-Company-Token", value="super-secret"),
        extra_endpoints=[
            EndpointConfig(hosts=["other.example.com"], auth=AuthConfig(type="none"))
        ],
    )
    result = tool._invoke(
        {"url": "https://api.example.com/go"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert responses.calls[0].request.headers["X-Company-Token"] == "super-secret"
    assert "X-Company-Token" not in responses.calls[1].request.headers


def test_credentials_stripped_when_only_the_port_changes(responses):
    """Same hostname, different port is still a different origin — `requests`
    would have kept the credential here."""
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "http://api.example.com:9999/collect"},
    )
    responses.get("http://api.example.com:9999/collect", status=200, json={"ok": True})

    tool = build_tool(
        hosts=["api.example.com"],  # any scheme, any port
        auth=AuthConfig(type="bearer", token="super-secret"),
    )
    result = tool._invoke(
        {"url": "https://api.example.com/go"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert responses.calls[0].request.headers["Authorization"] == "Bearer super-secret"
    assert "Authorization" not in responses.calls[1].request.headers


def test_credentials_kept_on_same_origin_redirect(responses):
    responses.get(
        "https://api.example.com/a",
        status=302,
        headers={"Location": "https://api.example.com/b"},
    )
    responses.get("https://api.example.com/b", status=200, json={"ok": True})

    tool = build_tool(auth=AuthConfig(type="bearer", token="super-secret"))
    tool._invoke(
        {"url": "https://api.example.com/a"}, create_mock_tool_invoke_context()
    )

    assert responses.calls[1].request.headers["Authorization"] == "Bearer super-secret"


def test_llm_supplied_cookie_stripped_on_cross_host_redirect(responses):
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "https://other.example.com/collect"},
    )
    responses.get("https://other.example.com/collect", status=200, json={"ok": True})

    tool = build_tool(
        extra_endpoints=[
            EndpointConfig(hosts=["other.example.com"], auth=AuthConfig(type="none"))
        ]
    )
    tool._invoke(
        {
            "url": "https://api.example.com/go",
            "headers": '{"Cookie": "session=secret"}',
        },
        create_mock_tool_invoke_context(),
    )

    assert responses.calls[0].request.headers["Cookie"] == "session=secret"
    assert "Cookie" not in responses.calls[1].request.headers


def test_default_headers_stripped_on_cross_host_redirect(responses):
    """Toolset-level `default_headers` can hold a secret too."""
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "https://other.example.com/collect"},
    )
    responses.get("https://other.example.com/collect", status=200, json={"ok": True})

    tool = build_tool(
        default_headers={"X-Tenant-Secret": "super-secret"},
        extra_endpoints=[
            EndpointConfig(hosts=["other.example.com"], auth=AuthConfig(type="none"))
        ],
    )
    tool._invoke(
        {"url": "https://api.example.com/go"}, create_mock_tool_invoke_context()
    )

    assert responses.calls[0].request.headers["X-Tenant-Secret"] == "super-secret"
    assert "X-Tenant-Secret" not in responses.calls[1].request.headers


def test_rendered_extra_headers_stripped_on_cross_host_redirect(responses):
    """`extra_headers` exists to inject tokens (e.g. "{{ env.MY_TOKEN }}"), so a
    rendered value must not cross an origin either."""
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "https://other.example.com/collect"},
    )
    responses.get("https://other.example.com/collect", status=200, json={"ok": True})

    tool = build_tool(
        extra_headers={"X-Rendered-Token": "super-secret"},
        extra_endpoints=[
            EndpointConfig(hosts=["other.example.com"], auth=AuthConfig(type="none"))
        ],
    )
    tool._invoke(
        {"url": "https://api.example.com/go"}, create_mock_tool_invoke_context()
    )

    assert responses.calls[0].request.headers["X-Rendered-Token"] == "super-secret"
    assert "X-Rendered-Token" not in responses.calls[1].request.headers


def test_only_allowlisted_headers_cross_an_origin(responses):
    """Cross-origin header handling is an allowlist, not a list of known
    credential names — an unrecognised header is dropped, not forwarded."""
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "https://other.example.com/collect"},
    )
    responses.get("https://other.example.com/collect", status=200, json={"ok": True})

    tool = build_tool(
        default_headers={"X-Some-Future-Secret-Channel": "super-secret"},
        extra_endpoints=[
            EndpointConfig(hosts=["other.example.com"], auth=AuthConfig(type="none"))
        ],
    )
    tool._invoke(
        {"url": "https://api.example.com/go"}, create_mock_tool_invoke_context()
    )

    forwarded = {k.lower() for k in responses.calls[1].request.headers}
    assert "x-some-future-secret-channel" not in forwarded
    # Content negotiation still works after the hop.
    assert responses.calls[1].request.headers.get("Accept") == "application/json"


# ---------------------------------------------------------------------------
# Method handling across a redirect
# ---------------------------------------------------------------------------


def test_302_downgrades_post_to_get_and_drops_body(responses):
    responses.post(
        "https://api.example.com/submit",
        status=302,
        headers={"Location": "https://api.example.com/done"},
    )
    responses.get("https://api.example.com/done", status=200, json={"ok": True})

    tool = build_tool(methods=["GET", "POST"])
    result = tool._invoke(
        {
            "url": "https://api.example.com/submit",
            "method": "POST",
            "body": '{"payload": "x"}',
        },
        create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert responses.calls[1].request.method == "GET"
    assert not responses.calls[1].request.body


def test_307_preserves_method_and_body(responses):
    responses.post(
        "https://api.example.com/submit",
        status=307,
        headers={"Location": "https://api.example.com/done"},
    )
    responses.post("https://api.example.com/done", status=200, json={"ok": True})

    tool = build_tool(methods=["GET", "POST"])
    result = tool._invoke(
        {
            "url": "https://api.example.com/submit",
            "method": "POST",
            "body": '{"payload": "x"}',
        },
        create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert responses.calls[1].request.method == "POST"
    assert responses.calls[1].request.body == '{"payload": "x"}'


def test_redirect_to_endpoint_disallowing_the_method_is_refused(responses):
    """The redirect target's own method whitelist is enforced too."""
    responses.post(
        "https://api.example.com/submit",
        status=307,
        headers={"Location": "https://readonly.example.com/write"},
    )
    register_trap(responses, "https://readonly.example.com/write", method="POST", body="written")

    tool = build_tool(
        methods=["GET", "POST"],
        extra_endpoints=[
            EndpointConfig(
                hosts=["readonly.example.com"],
                methods=["GET"],
                auth=AuthConfig(type="none"),
            )
        ],
    )
    result = tool._invoke(
        {"url": "https://api.example.com/submit", "method": "POST"},
        create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "not allowed for the redirect target" in result.error
    assert len(responses.calls) == 1


# ---------------------------------------------------------------------------
# Optional post-DNS internal-IP block
# ---------------------------------------------------------------------------


def test_block_internal_ips_rejects_whitelisted_host_resolving_internally(
    monkeypatch, responses
):
    internal_dns(monkeypatch, "169.254.169.254")
    register_trap(responses, "https://api.example.com/x", body="metadata")

    tool = build_tool(block_internal_ips=True)
    result = tool._invoke(
        {"url": "https://api.example.com/x"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "Refusing to request" in result.error
    assert len(responses.calls) == 0


def test_block_internal_ips_allows_public_host(monkeypatch, responses):
    public_dns(monkeypatch)
    responses.get("https://api.example.com/x", status=200, json={"ok": True})

    tool = build_tool(block_internal_ips=True)
    result = tool._invoke(
        {"url": "https://api.example.com/x"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.SUCCESS


def test_block_internal_ips_re_validates_each_redirect_hop(monkeypatch, responses):
    """Hop 1 resolves public, hop 2 resolves internal — the second hop must be
    rejected even though both hosts are whitelisted."""
    calls = {"n": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls["n"] += 1
        ip = "93.184.216.34" if calls["n"] == 1 else "169.254.169.254"
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake_getaddrinfo)
    responses.get(
        "https://api.example.com/go",
        status=302,
        headers={"Location": "https://other.example.com/next"},
    )
    register_trap(responses, "https://other.example.com/next", body="internal")

    tool = build_tool(
        block_internal_ips=True,
        extra_endpoints=[
            EndpointConfig(hosts=["other.example.com"], auth=AuthConfig(type="none"))
        ],
    )
    result = tool._invoke(
        {"url": "https://api.example.com/go"}, create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "Refusing to request" in result.error
    assert len(responses.calls) == 1


def test_internal_ips_reachable_by_default(monkeypatch, responses):
    """Whitelisted in-cluster endpoints must keep working out of the box —
    `block_internal_ips` defaults to False for exactly this reason."""
    internal_dns(monkeypatch, "10.0.0.5")
    responses.get("http://prometheus.monitoring.svc:9090/-/healthy", status=200, body="ok")

    tool = build_tool(hosts=["prometheus.monitoring.svc:9090"])
    result = tool._invoke(
        {"url": "http://prometheus.monitoring.svc:9090/-/healthy"},
        create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS


# ---------------------------------------------------------------------------
# Health check (operator-configured URL, but still redirect-attackable)
# ---------------------------------------------------------------------------


def test_health_check_refuses_off_origin_redirect(responses):
    responses.get(
        "https://api.example.com/health",
        status=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )
    register_trap(responses, "http://169.254.169.254/latest/meta-data/", body="imds")

    toolset = HttpToolset()
    success, message = toolset.prerequisites_callable(
        {
            "endpoints": [
                {
                    "hosts": ["api.example.com"],
                    "auth": {"type": "bearer", "token": "secret"},
                    "health_check_url": "https://api.example.com/health",
                }
            ]
        }
    )

    assert success is False
    assert "Refusing to follow redirect" in message
    assert len(responses.calls) == 1


def test_health_check_follows_same_origin_redirect(responses):
    """The health-check URL may sit outside the `paths` whitelist, so a
    same-origin redirect stays allowed."""
    responses.get(
        "https://api.example.com/health",
        status=302,
        headers={"Location": "https://api.example.com/healthz"},
    )
    responses.get("https://api.example.com/healthz", status=200, body="ok")

    toolset = HttpToolset()
    success, message = toolset.prerequisites_callable(
        {
            "endpoints": [
                {
                    "hosts": ["api.example.com"],
                    "paths": ["/v1/*"],
                    "auth": {"type": "none"},
                    "health_check_url": "https://api.example.com/health",
                }
            ]
        }
    )

    assert success is True, message
    assert len(responses.calls) == 2


# ---------------------------------------------------------------------------
# The unguarded primitive is gone
# ---------------------------------------------------------------------------


def test_requests_are_never_sent_with_redirects_enabled(responses, monkeypatch):
    """Belt and braces: no code path may hand the redirect decision back to
    `requests` (whose default is allow_redirects=True)."""
    seen = []
    real_request = http_toolset.requests.request

    def spy(method, url, **kwargs):
        seen.append(kwargs.get("allow_redirects"))
        return real_request(method, url, **kwargs)

    monkeypatch.setattr(http_toolset.requests, "request", spy)
    responses.get("https://api.example.com/x", status=200, json={"ok": True})

    tool = build_tool()
    tool._invoke(
        {"url": "https://api.example.com/x"}, create_mock_tool_invoke_context()
    )

    assert seen == [False]
