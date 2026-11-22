"""API authentication of the Holmes server (holmes/utils/auth.py).

Most tests run against the real `server.app`, with the server's auth settings
swapped per test, so a route added to server.py is covered automatically.
"""

import hashlib
import logging
import os
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.datastructures import URL
from starlette.routing import Mount, Route
from starlette.websockets import WebSocketDisconnect

import server
from holmes.admin.admin_api import admin_app
from holmes.utils.auth import (
    AUTH_EXEMPT_PATHS,
    UNAUTHORIZED_DETAIL,
    ApiAuthSettings,
    ApiAuthSettingsLoader,
    ApiKeyAuthMiddleware,
    is_valid_api_key,
    load_api_auth_settings,
)

USER_KEY = "user-key-12345"
DEFAULT_KEY = "chart-default-key-67890"


class _Fixed:
    def __init__(self, settings: ApiAuthSettings):
        self.settings = settings

    def get(self) -> ApiAuthSettings:
        return self.settings


def _settings(*, user: str = "", default: str = "", disabled: bool = False) -> ApiAuthSettings:
    keys = {}
    if user:
        keys["HOLMES_API_KEY"] = user
    if default:
        keys["HOLMES_DEFAULT_API_KEY_SHA256_FILE"] = default
    return ApiAuthSettings.from_keys(keys, disabled=disabled)


def _sha256_hex(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@pytest.fixture
def client_with(monkeypatch):
    """A TestClient on server.app with the given auth settings."""

    def make(settings: ApiAuthSettings) -> TestClient:
        monkeypatch.setattr(server, "api_auth", _Fixed(settings))
        mock_config = MagicMock()
        mock_config.get_models_list.return_value = ["test-model"]
        monkeypatch.setattr(server, "config", mock_config)
        return TestClient(server.app)

    return make


def _all_routes(app: FastAPI, prefix: str = "") -> List[Tuple[str, str]]:
    """(method, path) for every route of the app and of its mounted sub-apps."""
    out = []
    for route in app.routes:
        if isinstance(route, Mount):
            sub_app = route.app
            if isinstance(sub_app, FastAPI):
                out += _all_routes(sub_app, prefix + route.path)
        elif isinstance(route, (APIRoute, Route)):
            for method in sorted(route.methods or {"GET"}):
                if method != "HEAD":
                    out.append((method, prefix + route.path))
    return out


SERVER_ROUTES = _all_routes(server.app)


def test_route_inventory_covers_the_known_endpoints():
    """Guards the route walk itself: if this list shrinks, the walk broke."""
    paths = {p for _, p in SERVER_ROUTES}
    for expected in (
        "/api/chat",
        "/api/model",
        "/api/info",
        "/api/oauth/callback",
        "/api/checks/execute",
        "/api/checks/docs",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/healthz",
        "/readyz",
    ):
        assert expected in paths


# ---------------------------------------------------------------------------
# Every route is guarded, except the probes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", SERVER_ROUTES)
def test_every_route_needs_a_key_except_the_probes(client_with, method, path):
    client = client_with(_settings(user=USER_KEY, default=DEFAULT_KEY))
    response = client.request(method, path)
    if path in AUTH_EXEMPT_PATHS:
        assert response.status_code == 200, (method, path)
    else:
        assert response.status_code == 401, (method, path)
        assert response.json() == {"detail": UNAUTHORIZED_DETAIL}
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method,path", SERVER_ROUTES)
def test_no_key_configured_closes_every_route_except_the_probes(client_with, method, path):
    """Authentication is on by default: with no key at all, nothing is open."""
    client = client_with(_settings())
    response = client.request(method, path)
    expected = 200 if path in AUTH_EXEMPT_PATHS else 401
    assert response.status_code == expected, (method, path)


@pytest.mark.parametrize(
    "path", ["/healthz/", "/readyz/x", "/HEALTHZ", "/api/unknown", "/api/admin/reload"]
)
def test_unknown_and_lookalike_paths_answer_401_not_404(client_with, path):
    client = client_with(_settings(user=USER_KEY))
    assert client.post(path).status_code == 401
    assert client.get(path).status_code == 401


def test_mounted_admin_app_is_guarded():
    """ENABLE_ADMIN_API mounts /api/admin; the middleware guards mounts too."""
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware, get_settings=lambda: _settings(user=USER_KEY))
    app.mount("/api/admin", admin_app)
    client = TestClient(app)
    for path in ("/api/admin/reload", "/api/admin/reload/toolsets", "/api/admin/reload/models"):
        assert client.post(path).status_code == 401
    assert client.get("/api/admin/openapi.json").status_code == 401
    ok = client.get("/api/admin/openapi.json", headers={"X-API-Key": USER_KEY})
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# Valid credentials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"X-API-Key": USER_KEY},
        {"X-API-Key": DEFAULT_KEY},
        {"Authorization": f"Bearer {USER_KEY}"},
        {"Authorization": f"bearer {DEFAULT_KEY}"},
    ],
)
def test_either_key_is_accepted_in_either_header(client_with, headers):
    client = client_with(_settings(user=USER_KEY, default=DEFAULT_KEY))
    response = client.get("/api/model", headers=headers)
    assert response.status_code == 200
    assert "test-model" in response.json()["model_name"]


def test_default_key_alone_is_enough(client_with):
    """The runner and operator send the chart key even when no user key is set."""
    client = client_with(_settings(default=DEFAULT_KEY))
    assert client.get("/api/model", headers={"X-API-Key": DEFAULT_KEY}).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"X-API-Key": "wrong"},
        {"X-API-Key": USER_KEY + "x"},
        {"X-API-Key": USER_KEY[:-1]},
        {"Authorization": f"Basic {USER_KEY}"},
        {"Authorization": USER_KEY},
        {"X-API-Key": ""},
    ],
)
def test_wrong_or_malformed_credentials_are_rejected(client_with, headers):
    client = client_with(_settings(user=USER_KEY, default=DEFAULT_KEY))
    assert client.get("/api/model", headers=headers).status_code == 401


def test_disabled_opens_every_route(client_with):
    client = client_with(_settings(user=USER_KEY, disabled=True))
    assert client.get("/api/model").status_code == 200


def test_spoofed_url_path_does_not_bypass_auth(client_with, monkeypatch):
    """Regression for CVE-2026-48710 / GHSA-86qp-5c8j-p5mr.

    request.url is rebuilt from the Host header and can be forged; the
    exemption check must use the raw scope path. Forcing request.url to
    /healthz must not open a guarded route.
    """
    fake_url = URL("http://attacker.example/healthz")
    monkeypatch.setattr("starlette.requests.Request.url", property(lambda self: fake_url))
    client = client_with(_settings(user=USER_KEY))
    assert client.post("/api/chat").status_code == 401


def test_rejections_are_logged_without_the_key(client_with, caplog):
    client = client_with(_settings(user=USER_KEY))
    with caplog.at_level(logging.WARNING):
        client.get("/api/model", headers={"X-API-Key": "a-wrong-key-value"})
        client.get("/api/info")
    text = caplog.text
    assert "Unauthorized request: GET '/api/model' (invalid API key)" in text
    assert "Unauthorized request: GET '/api/info' (missing API key)" in text
    assert "a-wrong-key-value" not in text and USER_KEY not in text


def test_streaming_responses_pass_through_the_middleware():
    """The middleware is pure ASGI: request bodies and SSE streams are untouched."""
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware, get_settings=lambda: _settings(user=USER_KEY))

    @app.post("/stream")
    async def stream(request: Request):
        body = await request.body()

        def gen():
            yield b"data: " + body + b"\n\n"
            yield b"data: done\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    client = TestClient(app)
    response = client.post("/stream", content=b"hello", headers={"X-API-Key": USER_KEY})
    assert response.status_code == 200
    assert response.text == "data: hello\n\ndata: done\n\n"


def test_websocket_without_key_is_refused():
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware, get_settings=lambda: _settings(user=USER_KEY))

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass
    with client.websocket_connect("/ws", headers={"X-API-Key": USER_KEY}) as ws_conn:
        assert ws_conn.receive_text() == "hi"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_from_env_and_hash_file(tmp_path):
    hash_file = tmp_path / "token-sha256"
    hash_file.write_text(f"  {_sha256_hex('from-file').upper()} \n")
    settings = load_api_auth_settings(
        {
            "HOLMES_API_KEY": " user ",
            "HOLMES_DEFAULT_API_KEY": "inline-default",
            "HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(hash_file),
        }
    )
    assert list(settings.key_digests) == [
        "HOLMES_API_KEY",
        "HOLMES_DEFAULT_API_KEY",
        "HOLMES_DEFAULT_API_KEY_SHA256_FILE",
    ]
    for key in ("user", "inline-default", "from-file"):
        assert is_valid_api_key(key, settings)
    assert settings.enforced


def test_hash_file_key_is_not_accepted_as_its_own_hash(tmp_path):
    """Holmes holds only the hash: presenting the hash file's content (what a
    tool reading the Holmes container would find) must not authenticate."""
    hash_file = tmp_path / "token-sha256"
    hash_file.write_text(_sha256_hex(DEFAULT_KEY))
    settings = load_api_auth_settings({"HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(hash_file)})
    assert is_valid_api_key(DEFAULT_KEY, settings)
    assert not is_valid_api_key(_sha256_hex(DEFAULT_KEY), settings)


@pytest.mark.parametrize(
    "content",
    ["not-a-hash", "chart-default-key-67890", "ab" * 31, "zz" * 32, "ab" * 33],
)
def test_malformed_hash_file_is_ignored(tmp_path, caplog, content):
    """A plaintext key or a truncated hash in the hash file is not a key."""
    hash_file = tmp_path / "token-sha256"
    hash_file.write_text(content)
    with caplog.at_level(logging.WARNING):
        settings = load_api_auth_settings({"HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(hash_file)})
    assert settings.key_digests == {}
    assert not is_valid_api_key(content, settings)
    assert "64 hex characters" in caplog.text
    assert content not in caplog.text


def test_whitespace_only_and_missing_keys_are_not_keys(tmp_path):
    settings = load_api_auth_settings(
        {
            "HOLMES_API_KEY": "   ",
            "HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(tmp_path / "missing"),
        }
    )
    assert settings.key_digests == {}
    assert settings.enforced
    assert not is_valid_api_key("", settings)


@pytest.mark.parametrize("value,disabled", [("true", True), ("TRUE", True), ("false", False), ("", False)])
def test_auth_disabled_values(value, disabled):
    assert load_api_auth_settings({"HOLMES_AUTH_DISABLED": value}).disabled is disabled


@pytest.mark.parametrize("value", ["1", "yes", "on", "flase"])
def test_auth_disabled_typo_fails_instead_of_guessing(value):
    with pytest.raises(ValueError, match="HOLMES_AUTH_DISABLED"):
        load_api_auth_settings({"HOLMES_AUTH_DISABLED": value})


def test_startup_messages():
    level, msg = _settings(user=USER_KEY, default=DEFAULT_KEY).startup_message()
    assert level == logging.INFO
    assert "HOLMES_API_KEY, HOLMES_DEFAULT_API_KEY_SHA256_FILE" in msg
    assert USER_KEY not in msg and DEFAULT_KEY not in msg

    level, msg = _settings().startup_message()
    assert level == logging.WARNING and "answers 401" in msg

    level, msg = ApiAuthSettings(hash_file="/etc/holmes/api-key/token-sha256").startup_message()
    assert "/etc/holmes/api-key/token-sha256 (Secret key token-sha256) is missing or empty" in msg

    level, msg = _settings(disabled=True).startup_message()
    assert level == logging.WARNING and "disabled" in msg


def test_loader_picks_up_a_hash_file_that_appears_and_changes(tmp_path):
    """Kubelet fills an optional Secret volume once the hook has created the
    Secret, and swaps it when the Secret is recreated: no restart needed."""
    hash_file = tmp_path / "token-sha256"
    loader = ApiAuthSettingsLoader({"HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(hash_file)})
    assert loader.get().key_digests == {}

    hash_file.write_text(_sha256_hex("first"))
    assert is_valid_api_key("first", loader.get())

    # Kubelet swaps a symlink; a rename gives the path a new inode the same way.
    replacement = tmp_path / "token-sha256.new"
    replacement.write_text(_sha256_hex("second-key"))
    os.replace(replacement, hash_file)
    assert is_valid_api_key("second-key", loader.get())
    assert not is_valid_api_key("first", loader.get())

    hash_file.unlink()
    assert loader.get().key_digests == {}


def test_loader_keeps_user_key_while_the_file_changes(tmp_path):
    hash_file = tmp_path / "token-sha256"
    loader = ApiAuthSettingsLoader(
        {"HOLMES_API_KEY": "mine", "HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(hash_file)}
    )
    hash_file.write_text(_sha256_hex("chart"))
    settings = loader.get()
    assert is_valid_api_key("mine", settings)
    assert is_valid_api_key("chart", settings)


# ---------------------------------------------------------------------------
# The key stays out of the app
# ---------------------------------------------------------------------------


def _echo_headers_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware, get_settings=lambda: _settings(user=USER_KEY))

    @app.get("/echo")
    async def echo(request: Request):
        return {"headers": {k.lower(): v for k, v in request.headers.items()}}

    return app


@pytest.mark.parametrize(
    "headers,removed,kept",
    [
        ({"X-API-Key": USER_KEY}, "x-api-key", None),
        ({"Authorization": f"Bearer {USER_KEY}"}, "authorization", None),
        # A user token in Authorization (e.g. for header passthrough) is not
        # the Holmes key and stays when the key came in X-API-Key.
        ({"X-API-Key": USER_KEY, "Authorization": "Bearer user-token"}, "x-api-key", "authorization"),
    ],
)
def test_the_header_carrying_the_key_is_removed_after_authentication(headers, removed, kept):
    """Holmes passes request headers through to toolsets, and Sentry records
    request headers and every frame's scope: neither must see the key."""
    seen = TestClient(_echo_headers_app()).get("/echo", headers={**headers, "X-Other": "1"}).json()["headers"]
    assert removed not in seen
    assert USER_KEY not in str(seen)
    assert seen["x-other"] == "1"
    if kept:
        assert seen[kept] == headers["Authorization"]


def test_auth_is_the_outermost_middleware_of_the_server():
    """Middleware added later would run before auth and see unauthenticated requests."""
    assert server.app.user_middleware[0].cls is ApiKeyAuthMiddleware


def test_rejected_paths_are_logged_on_one_line(client_with, caplog):
    client = client_with(_settings(user=USER_KEY))
    with caplog.at_level(logging.WARNING):
        client.get("/api/x%0aFAKE LOG LINE")
    assert "Unauthorized request: GET '/api/x\\nFAKE LOG LINE'" in caplog.text


def test_hash_file_with_invalid_bytes_is_ignored_not_fatal(tmp_path, caplog):
    """A non-text hash file must not stop the server (a valid HOLMES_API_KEY still works)."""
    hash_file = tmp_path / "token-sha256"
    hash_file.write_bytes(b"\xff\xfe" + b"a" * 62)
    with caplog.at_level(logging.WARNING):
        settings = load_api_auth_settings(
            {"HOLMES_API_KEY": USER_KEY, "HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(hash_file)}
        )
    assert list(settings.key_digests) == ["HOLMES_API_KEY"]
    assert "64 hex characters" in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files regardless of mode")
def test_loader_retries_a_hash_file_it_could_not_read(tmp_path):
    """A read failure is not cached: once the file is readable again, with the
    same inode, size and mtime, the next request picks the key up."""
    hash_file = tmp_path / "token-sha256"
    hash_file.write_text(_sha256_hex("chart"))
    stat_before = hash_file.stat()
    hash_file.chmod(0)
    try:
        loader = ApiAuthSettingsLoader({"HOLMES_DEFAULT_API_KEY_SHA256_FILE": str(hash_file)})
        assert not is_valid_api_key("chart", loader.get())
    finally:
        hash_file.chmod(0o600)
    os.utime(hash_file, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
    assert is_valid_api_key("chart", loader.get())
