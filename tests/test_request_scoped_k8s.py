"""Tests for request-scoped Kubernetes credentials."""

import os

import pytest
import yaml

from holmes.core.request_scoped_k8s import (
    build_request_scoped_env,
    cleanup_kubeconfig,
    extract_request_token,
    is_request_token_mode,
)

AUTH_MODE = "HOLMES_K8S_AUTH_MODE"
TOKEN_HEADERS = "HOLMES_K8S_REQUEST_TOKEN_HEADERS"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        AUTH_MODE,
        TOKEN_HEADERS,
        "HOLMES_K8S_API_SERVER",
        "HOLMES_K8S_CA_CERT",
        "HOLMES_K8S_INSECURE_SKIP_TLS_VERIFY",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_disabled_by_default(monkeypatch):
    assert is_request_token_mode() is False
    env, path = build_request_scoped_env({"headers": {"Authorization": "Bearer abc"}})
    assert env is None
    assert path is None


def test_request_token_mode_no_token_is_noop(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    env, path = build_request_scoped_env({"headers": {"X-Other": "value"}})
    assert env is None and path is None


def test_extract_strips_bearer_and_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    # Starlette lowercases header names; default allow-list uses mixed case.
    token = extract_request_token({"headers": {"authorization": "Bearer TOK123"}})
    assert token == "TOK123"


def test_header_priority_order(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    request_context = {
        "headers": {
            "Authorization": "Bearer from-authz",
            "X-Auth-Request-Id-Token": "from-oauth2proxy",
        }
    }
    # Default order prefers X-Auth-Request-Id-Token over Authorization.
    assert extract_request_token(request_context) == "from-oauth2proxy"


def test_custom_header_list(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    monkeypatch.setenv(TOKEN_HEADERS, "X-Id-Token")
    request_context = {
        "headers": {"X-Id-Token": "custom", "Authorization": "Bearer ignored"}
    }
    assert extract_request_token(request_context) == "custom"


def test_kubeconfig_is_per_invocation_and_cleaned_up(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    ca = tmp_path / "ca.crt"
    ca.write_text("CADATA")
    monkeypatch.setenv("HOLMES_K8S_CA_CERT", str(ca))
    monkeypatch.setenv("HOLMES_K8S_API_SERVER", "https://api.example:6443")

    request_context = {"headers": {"Authorization": "Bearer SECRET"}}
    env, path = build_request_scoped_env(request_context)

    assert env is not None and "KUBECONFIG" in env
    assert path is not None and os.path.isfile(path)
    # 0600 permissions: not readable/writable by group or others.
    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600

    content = open(path).read()
    assert "token: 'SECRET'" in content
    assert "Bearer" not in content  # bearer prefix must be stripped
    assert "server: 'https://api.example:6443'" in content
    assert f"certificate-authority: '{ca}'" in content

    # Two concurrent requests must get distinct kubeconfig files (no shared state).
    _, path2 = build_request_scoped_env(request_context)
    assert path2 != path

    cleanup_kubeconfig(path)
    cleanup_kubeconfig(path2)
    assert not os.path.exists(path)
    assert not os.path.exists(path2)


def test_cleanup_missing_path_is_safe():
    cleanup_kubeconfig(None)
    cleanup_kubeconfig("/nonexistent/holmes-kubeconfig-xyz.yaml")


def test_missing_ca_does_not_silently_disable_tls_verification(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    monkeypatch.setenv("HOLMES_K8S_CA_CERT", "/definitely/not/here.crt")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T"}})
    try:
        content = open(path).read()
        assert "insecure-skip-tls-verify" not in content
        assert "certificate-authority" not in content
    finally:
        cleanup_kubeconfig(path)


def test_insecure_skip_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    monkeypatch.setenv("HOLMES_K8S_CA_CERT", "/definitely/not/here.crt")
    monkeypatch.setenv("HOLMES_K8S_INSECURE_SKIP_TLS_VERIFY", "true")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T"}})
    try:
        assert "insecure-skip-tls-verify: true" in open(path).read()
    finally:
        cleanup_kubeconfig(path)


@pytest.mark.parametrize(
    "bad_token",
    [
        "tok\nusers:\n- name: attacker",  # YAML injection via newline
        "tok\rmore",
        "tok\x00null",
        "has space",
    ],
)
def test_malformed_tokens_are_rejected(monkeypatch, bad_token):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    assert extract_request_token({"headers": {"Authorization": bad_token}}) is None
    env, path = build_request_scoped_env({"headers": {"Authorization": bad_token}})
    assert env is None and path is None


def test_token_with_quote_is_escaped_not_injected(monkeypatch, tmp_path):
    """A quote in the credential must stay inside the scalar, not break out."""
    monkeypatch.setenv(AUTH_MODE, "request_token")
    _, path = build_request_scoped_env({"headers": {"Authorization": "ab'cd"}})
    try:
        content = open(path).read()
        assert "token: 'ab''cd'" in content
        parsed = yaml.safe_load(content)
        assert parsed["users"][0]["user"]["token"] == "ab'cd"
        # The document must still contain exactly the structure we intended.
        assert len(parsed["clusters"]) == 1 and len(parsed["users"]) == 1
    finally:
        cleanup_kubeconfig(path)


def test_rendered_kubeconfig_is_valid_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    ca = tmp_path / "ca.crt"
    ca.write_text("CADATA")
    monkeypatch.setenv("HOLMES_K8S_CA_CERT", str(ca))
    monkeypatch.setenv("HOLMES_K8S_API_SERVER", "https://api.example:6443")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T0K"}})
    try:
        cfg = yaml.safe_load(open(path).read())
        assert cfg["current-context"] == "holmes-request-scoped"
        assert cfg["clusters"][0]["cluster"]["server"] == "https://api.example:6443"
        assert cfg["clusters"][0]["cluster"]["certificate-authority"] == str(ca)
        assert cfg["users"][0]["user"]["token"] == "T0K"
    finally:
        cleanup_kubeconfig(path)


def test_api_server_defaults_to_in_cluster_endpoint(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T"}})
    try:
        assert "server: 'https://10.0.0.1:6443'" in open(path).read()
    finally:
        cleanup_kubeconfig(path)


def test_api_server_falls_back_to_default_service_dns(monkeypatch):
    """With no override and no in-cluster env vars, use kubernetes.default.svc."""
    monkeypatch.setenv(AUTH_MODE, "request_token")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T"}})
    try:
        assert "server: 'https://kubernetes.default.svc'" in open(path).read()
    finally:
        cleanup_kubeconfig(path)


def test_ipv6_api_server_host_is_bracketed(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "fd00::1")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T"}})
    try:
        assert "server: 'https://[fd00::1]:443'" in open(path).read()
    finally:
        cleanup_kubeconfig(path)


@pytest.mark.parametrize(
    "bad_server",
    ["http://api.example:6443", "api.example:6443", "https://", "ftp://api.example"],
)
def test_non_https_api_server_override_is_rejected(monkeypatch, bad_server):
    """A cleartext or malformed endpoint must never receive the user's token."""
    monkeypatch.setenv(AUTH_MODE, "request_token")
    monkeypatch.setenv("HOLMES_K8S_API_SERVER", bad_server)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T"}})
    try:
        server = yaml.safe_load(open(path).read())["clusters"][0]["cluster"]["server"]
        # Falls back to in-cluster discovery rather than honouring the override.
        assert server == "https://10.0.0.1:6443"
    finally:
        cleanup_kubeconfig(path)


def test_https_api_server_override_is_accepted_case_insensitively(monkeypatch):
    monkeypatch.setenv(AUTH_MODE, "request_token")
    monkeypatch.setenv("HOLMES_K8S_API_SERVER", "HTTPS://API.EXAMPLE:6443")
    _, path = build_request_scoped_env({"headers": {"Authorization": "Bearer T"}})
    try:
        assert "server: 'HTTPS://API.EXAMPLE:6443'" in open(path).read()
    finally:
        cleanup_kubeconfig(path)
