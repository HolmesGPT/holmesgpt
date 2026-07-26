"""ROB-4017: pin the wiring that hands postgrest a thread-safe HTTP/1.1 client.

SupabaseDal must build its httpx client on ``SupabaseRetryTransport`` and pass it
to postgrest via ``ClientOptions(httpx_client=...)`` so postgrest doesn't build
its own HTTP/2 client (see ``SupabaseRetryTransport`` for why). Because a custom
transport is supplied, ``http2``/``verify`` live on the transport while
``timeout``/``follow_redirects`` stay on the client.

Deterministic (no network); retry behaviour is covered in
``test_supabase_dal_retry.py``.
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.supabase_dal import SUPABASE_TIMEOUT_SECONDS, SupabaseDal


def _ui_token() -> str:
    bundle = {
        "store_url": "https://example.supabase.co",
        "api_key": "anon-key",
        "account_id": "acc-1",
        "email": "svc@example.com",
        "password": "pw",
    }
    return base64.b64encode(json.dumps(bundle).encode()).decode()


def _build_dal(monkeypatch, ca_env=None):
    """Construct a SupabaseDal with network mocked, capturing the kwargs passed
    to ``SupabaseRetryTransport`` and ``httpx.Client``, the
    ``ssl.create_default_context`` call (if any), and the ``ClientOptions``
    handed to ``create_client``."""
    monkeypatch.setenv("ROBUSTA_UI_TOKEN", _ui_token())
    # Start from a clean CA-env slate; the test harness/sandbox may set these.
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    for k, v in (ca_env or {}).items():
        monkeypatch.setenv(k, v)

    captured: dict = {}
    ssl_ctx_sentinel = MagicMock(name="ssl_context")
    transport_sentinel = MagicMock(name="transport")

    def fake_transport(*args, **kwargs):
        captured["transport_kwargs"] = kwargs
        return transport_sentinel

    def fake_httpx_client(*args, **kwargs):
        captured["httpx_kwargs"] = kwargs
        client = MagicMock(name="httpx_client")
        captured["created_client"] = client
        return client

    def fake_create_default_context(*args, **kwargs):
        captured["ssl_ctx_kwargs"] = kwargs
        return ssl_ctx_sentinel

    with (
        patch(
            "holmes.core.supabase_dal.SupabaseRetryTransport",
            side_effect=fake_transport,
        ),
        patch(
            "holmes.core.supabase_dal.httpx.Client", side_effect=fake_httpx_client
        ),
        patch(
            "holmes.core.supabase_dal.ssl.create_default_context",
            side_effect=fake_create_default_context,
        ),
        patch("holmes.core.supabase_dal.create_client") as mock_create,
        patch.object(SupabaseDal, "sign_in", return_value="user-1"),
        patch.object(SupabaseDal, "patch_postgrest_execute"),
    ):
        dal = SupabaseDal(cluster="test-cluster")
        # create_client(self.url, self.api_key, options) -> options is args[2]
        captured["options"] = mock_create.call_args.args[2]
    captured["ssl_ctx_sentinel"] = ssl_ctx_sentinel
    captured["transport_sentinel"] = transport_sentinel
    return dal, captured


def test_dal_disables_http2_on_the_transport(monkeypatch):
    dal, cap = _build_dal(monkeypatch)
    assert dal.enabled is True
    # http2 is disabled on the transport (httpx ignores http2 on the client when
    # a custom transport is supplied).
    assert cap["transport_kwargs"]["http2"] is False
    # timeout + redirect handling stay on the client.
    assert cap["httpx_kwargs"]["follow_redirects"] is True
    assert cap["httpx_kwargs"]["timeout"] == SUPABASE_TIMEOUT_SECONDS


def test_dal_client_uses_our_transport_and_forwards_client_to_postgrest(monkeypatch):
    # The client must be built on our SupabaseRetryTransport, and that exact
    # client must be handed to postgrest via ClientOptions so postgrest reuses it
    # instead of building its own http2=True client. Compare against the
    # instances the fakes actually created (not values read back from options,
    # which would be tautological).
    _, cap = _build_dal(monkeypatch)
    assert cap["httpx_kwargs"]["transport"] is cap["transport_sentinel"]
    assert cap["created_client"] is not None
    assert cap["options"].httpx_client is cap["created_client"]


def test_dal_verify_defaults_to_true_without_ca_env(monkeypatch):
    _, cap = _build_dal(monkeypatch)
    # No CA env -> verify=True on the transport and no SSLContext is built.
    assert cap["transport_kwargs"]["verify"] is True
    assert "ssl_ctx_kwargs" not in cap


def test_dal_builds_sslcontext_not_string_for_verify(monkeypatch):
    # Forward-compatible with httpx: verify must be an SSLContext, never a path
    # string (httpx deprecated `verify=<str>`).
    _, cap = _build_dal(monkeypatch, ca_env={"SSL_CERT_FILE": "/etc/ssl/custom-ca.pem"})
    assert cap["transport_kwargs"]["verify"] is cap["ssl_ctx_sentinel"]
    assert not isinstance(cap["transport_kwargs"]["verify"], str)


def test_dal_honors_ssl_cert_file_as_cafile(monkeypatch):
    _, cap = _build_dal(monkeypatch, ca_env={"SSL_CERT_FILE": "/etc/ssl/custom-ca.pem"})
    assert cap["ssl_ctx_kwargs"] == {"cafile": "/etc/ssl/custom-ca.pem"}


def test_dal_honors_requests_ca_bundle_as_cafile(monkeypatch):
    _, cap = _build_dal(
        monkeypatch, ca_env={"REQUESTS_CA_BUNDLE": "/etc/ssl/proxy-ca.pem"}
    )
    assert cap["ssl_ctx_kwargs"] == {"cafile": "/etc/ssl/proxy-ca.pem"}


def test_dal_ssl_cert_file_takes_precedence_over_requests_ca_bundle(monkeypatch):
    # Mirrors the code: SSL_CERT_FILE is checked before REQUESTS_CA_BUNDLE.
    _, cap = _build_dal(
        monkeypatch,
        ca_env={
            "SSL_CERT_FILE": "/etc/ssl/custom-ca.pem",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/proxy-ca.pem",
        },
    )
    assert cap["ssl_ctx_kwargs"] == {"cafile": "/etc/ssl/custom-ca.pem"}


def test_dal_uses_capath_when_bundle_is_a_directory(monkeypatch, tmp_path):
    # A CA *directory* must be passed as capath, not cafile.
    _, cap = _build_dal(monkeypatch, ca_env={"SSL_CERT_FILE": str(tmp_path)})
    assert cap["ssl_ctx_kwargs"] == {"capath": str(tmp_path)}


@pytest.mark.parametrize("ca_env", [None, {"SSL_CERT_FILE": "/etc/ssl/custom-ca.pem"}])
def test_dal_always_disables_http2_regardless_of_ca(monkeypatch, ca_env):
    # http2 must stay disabled no matter the CA configuration.
    _, cap = _build_dal(monkeypatch, ca_env=ca_env)
    assert cap["transport_kwargs"]["http2"] is False


_PROXY_ENV = ("https_proxy", "http_proxy", "all_proxy", "no_proxy")


def _clear_proxy_env(monkeypatch):
    for name in _PROXY_ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


def test_dal_routes_supabase_through_https_proxy_from_env(monkeypatch):
    # Regression (ROB-4017): a custom transport makes httpx ignore env proxies, so
    # we must resolve and pass the proxy ourselves or proxied clusters break.
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://proxy.corp:8080")
    _, cap = _build_dal(monkeypatch)
    assert cap["transport_kwargs"]["proxy"] == "http://proxy.corp:8080"


def test_dal_skips_proxy_when_supabase_host_in_no_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://proxy.corp:8080")
    monkeypatch.setenv("no_proxy", "example.supabase.co")
    _, cap = _build_dal(monkeypatch)
    assert cap["transport_kwargs"]["proxy"] is None


def test_dal_skips_proxy_when_no_proxy_entry_has_port(monkeypatch):
    # no_proxy entries may include a port; the default HTTPS port must match.
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://proxy.corp:8080")
    monkeypatch.setenv("no_proxy", "example.supabase.co:443")
    _, cap = _build_dal(monkeypatch)
    assert cap["transport_kwargs"]["proxy"] is None


def test_dal_no_proxy_when_env_unset(monkeypatch):
    _clear_proxy_env(monkeypatch)
    # Ignore any OS-level proxy config so the assertion is host-independent.
    monkeypatch.setattr("holmes.core.supabase_dal.getproxies", lambda: {})
    _, cap = _build_dal(monkeypatch)
    assert cap["transport_kwargs"]["proxy"] is None
