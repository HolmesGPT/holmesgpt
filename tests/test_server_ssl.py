"""Unit tests for build_ssl_kwargs() — the config-driven HTTP/HTTPS switch.

build_ssl_kwargs() reads the HOLMES_SSL_* values that server.py binds at import
time, so we patch those module-level symbols on `server` rather than the
environment. The helper only checks that the cert/key/CA paths exist (it does not
parse them), so dummy files under tmp_path are sufficient.
"""

import os
import ssl

import pytest

import server


@pytest.fixture(autouse=True)
def _reset_ssl_env(monkeypatch):
    """Baseline every test to "no TLS configured" so cases are independent."""
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", "")
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", "")
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE_PASSWORD", "")
    monkeypatch.setattr(server, "HOLMES_SSL_CA_CERTS", "")


@pytest.fixture
def cert_and_key(tmp_path):
    cert = tmp_path / "tls.crt"
    key = tmp_path / "tls.key"
    cert.write_text("dummy-cert")
    key.write_text("dummy-key")
    return str(cert), str(key)


def test_no_tls_config_returns_empty(monkeypatch):
    assert server.build_ssl_kwargs() == {}


def test_only_certfile_fails_fast(monkeypatch, cert_and_key):
    cert, _ = cert_and_key
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", cert)
    with pytest.raises(SystemExit):
        server.build_ssl_kwargs()


def test_only_keyfile_fails_fast(monkeypatch, cert_and_key):
    _, key = cert_and_key
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", key)
    with pytest.raises(SystemExit):
        server.build_ssl_kwargs()


def test_missing_cert_file_fails_fast(monkeypatch, tmp_path, cert_and_key):
    _, key = cert_and_key
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", str(tmp_path / "nope.crt"))
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", key)
    with pytest.raises(SystemExit):
        server.build_ssl_kwargs()


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root bypasses file permissions, so chmod 000 stays readable"
)
def test_unreadable_cert_file_fails_fast(monkeypatch, cert_and_key):
    # A file that exists but isn't readable (e.g. wrong permissions on a mounted
    # secret) must fail fast here, not later inside uvicorn.run.
    cert, key = cert_and_key
    os.chmod(cert, 0o000)
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", cert)
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", key)
    with pytest.raises(SystemExit):
        server.build_ssl_kwargs()


def test_valid_cert_and_key(monkeypatch, cert_and_key):
    cert, key = cert_and_key
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", cert)
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", key)
    assert server.build_ssl_kwargs() == {"ssl_certfile": cert, "ssl_keyfile": key}


def test_keyfile_password_included(monkeypatch, cert_and_key):
    cert, key = cert_and_key
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", cert)
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", key)
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE_PASSWORD", "s3cret")
    kwargs = server.build_ssl_kwargs()
    assert kwargs["ssl_keyfile_password"] == "s3cret"


def test_mtls_ca_certs_enables_client_verification(monkeypatch, tmp_path, cert_and_key):
    cert, key = cert_and_key
    ca = tmp_path / "ca.crt"
    ca.write_text("dummy-ca")
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", cert)
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", key)
    monkeypatch.setattr(server, "HOLMES_SSL_CA_CERTS", str(ca))
    kwargs = server.build_ssl_kwargs()
    assert kwargs["ssl_ca_certs"] == str(ca)
    assert kwargs["ssl_cert_reqs"] == ssl.CERT_REQUIRED


def test_missing_ca_certs_file_fails_fast(monkeypatch, tmp_path, cert_and_key):
    cert, key = cert_and_key
    monkeypatch.setattr(server, "HOLMES_SSL_CERTFILE", cert)
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE", key)
    monkeypatch.setattr(server, "HOLMES_SSL_CA_CERTS", str(tmp_path / "missing-ca.crt"))
    with pytest.raises(SystemExit):
        server.build_ssl_kwargs()


def test_ca_certs_without_cert_and_key_fails_fast(monkeypatch, tmp_path):
    # mTLS without a server cert/key would otherwise silently downgrade to HTTP.
    ca = tmp_path / "ca.crt"
    ca.write_text("dummy-ca")
    monkeypatch.setattr(server, "HOLMES_SSL_CA_CERTS", str(ca))
    with pytest.raises(SystemExit):
        server.build_ssl_kwargs()


def test_keyfile_password_without_cert_and_key_fails_fast(monkeypatch):
    monkeypatch.setattr(server, "HOLMES_SSL_KEYFILE_PASSWORD", "s3cret")
    with pytest.raises(SystemExit):
        server.build_ssl_kwargs()
