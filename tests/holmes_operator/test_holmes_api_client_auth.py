"""The operator sends the Holmes API key on every check (holmes_api_client.py).

The Helm chart mounts the default key as a file and sets HOLMES_API_KEY_FILE;
the file is re-read per request so a rotated Secret is used without a restart.
An inline HOLMES_API_KEY wins.
"""

from typing import List, Optional

import httpx
import pytest

from holmes_operator.client.holmes_api_client import HolmesAPIClient

OK = {"status": "pass", "message": "ok", "duration": 0.1}


def _client(seen: List[Optional[str]], api_key_file: str = "") -> HolmesAPIClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-API-Key"))
        return httpx.Response(200, json=OK)

    client = HolmesAPIClient("http://holmes:80", api_key_file=api_key_file)
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def _check(client: HolmesAPIClient) -> None:
    await client.execute_check(
        check_name="c", query="q", timeout=10, mode="monitor", destinations=[]
    )


@pytest.fixture(autouse=True)
def _no_inline_key(monkeypatch):
    monkeypatch.delenv("HOLMES_API_KEY", raising=False)


async def test_key_file_is_sent_and_re_read_on_every_request(tmp_path):
    key_file = tmp_path / "token"
    key_file.write_text("first-key\n")
    seen: List[Optional[str]] = []
    client = _client(seen, str(key_file))

    await _check(client)
    key_file.write_text("rotated-key")
    await _check(client)

    assert seen == ["first-key", "rotated-key"]


async def test_inline_key_wins_over_the_file(tmp_path, monkeypatch):
    key_file = tmp_path / "token"
    key_file.write_text("file-key")
    monkeypatch.setenv("HOLMES_API_KEY", " own-key ")
    seen: List[Optional[str]] = []

    await _check(_client(seen, str(key_file)))

    assert seen == ["own-key"]


@pytest.mark.parametrize("configured", [False, True])
async def test_no_key_sends_no_header(tmp_path, configured):
    """No key configured, or the optional Secret not mounted yet: no header,
    and Holmes answers 401 rather than the operator failing to start."""
    seen: List[Optional[str]] = []
    key_file = str(tmp_path / "missing") if configured else ""

    await _check(_client(seen, key_file))

    assert seen == [None]


# ---------------------------------------------------------------------------
# HTTPS: the key only travels to a server whose certificate the operator trusts
# ---------------------------------------------------------------------------


@pytest.fixture
def https_holmes(tmp_path):
    """A local HTTPS server with a self-signed certificate for localhost,
    recording the X-API-Key it receives."""
    import json
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from tests.plugins.toolsets.test_prometheus_ssl import generate_self_signed_cert

    cert, key = tmp_path / "tls.crt", tmp_path / "tls.key"
    generate_self_signed_cert(str(cert), str(key))
    seen: List[Optional[str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append(self.headers.get("X-API-Key"))
            body = json.dumps(OK).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://localhost:{server.server_address[1]}", str(cert), seen
    server.shutdown()


async def test_https_with_the_holmes_ca_sends_the_key(https_holmes, monkeypatch):
    url, ca_file, seen = https_holmes
    monkeypatch.setenv("HOLMES_API_KEY", "op-key")
    await _check(HolmesAPIClient(url, ca_file=ca_file))
    assert seen == ["op-key"]


@pytest.mark.parametrize("ca_file", ["", "/nonexistent/ca.crt"])
async def test_https_with_an_untrusted_certificate_never_sends_the_key(https_holmes, monkeypatch, ca_file):
    """Without the CA (none configured, or the optional mount empty), the
    system trust store rejects the certificate before any request is sent."""
    url, _, seen = https_holmes
    monkeypatch.setenv("HOLMES_API_KEY", "op-key")
    client = HolmesAPIClient(url, ca_file=ca_file)
    # The client's own connection, without execute_check's retry/backoff.
    with pytest.raises(httpx.ConnectError):
        await client.client.post(f"{url}/api/checks/execute", json={}, headers=client._auth_headers())
    assert seen == []
