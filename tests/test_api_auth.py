"""API authentication of the Holmes server (holmes/utils/auth.py).

Most tests run against the real `server.app`, with the server's auth settings
swapped per test, so a route added to server.py is covered automatically.
"""

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
        keys["HOLMES_DEFAULT_API_KEY_FILE"] = default
    return ApiAuthSettings(keys=keys, disabled=disabled)


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
    assert "Unauthorized request: GET /api/model (invalid API key)" in text
    assert "Unauthorized request: GET /api/info (missing API key)" in text
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


def test_settings_from_env_and_file(tmp_path):
    key_file = tmp_path / "token"
    key_file.write_text("  from-file \n")
    settings = load_api_auth_settings(
        {
            "HOLMES_API_KEY": " user ",
            "HOLMES_DEFAULT_API_KEY": "inline-default",
            "HOLMES_DEFAULT_API_KEY_FILE": str(key_file),
        }
    )
    assert settings.keys == {
        "HOLMES_API_KEY": "user",
        "HOLMES_DEFAULT_API_KEY": "inline-default",
        "HOLMES_DEFAULT_API_KEY_FILE": "from-file",
    }
    assert settings.enforced


def test_whitespace_only_and_missing_keys_are_not_keys(tmp_path):
    settings = load_api_auth_settings(
        {"HOLMES_API_KEY": "   ", "HOLMES_DEFAULT_API_KEY_FILE": str(tmp_path / "missing")}
    )
    assert settings.keys == {}
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
    assert "HOLMES_API_KEY, HOLMES_DEFAULT_API_KEY_FILE" in msg
    assert USER_KEY not in msg and DEFAULT_KEY not in msg

    level, msg = _settings().startup_message()
    assert level == logging.WARNING and "answers 401" in msg

    level, msg = ApiAuthSettings(key_file="/etc/holmes/api-key/token").startup_message()
    assert "/etc/holmes/api-key/token is missing or empty" in msg

    level, msg = _settings(disabled=True).startup_message()
    assert level == logging.WARNING and "disabled" in msg


def test_loader_picks_up_a_key_file_that_appears_and_changes(tmp_path):
    """Kubelet fills an optional Secret volume once the hook has created the
    Secret, and swaps it when the Secret is recreated: no restart needed."""
    key_file = tmp_path / "token"
    loader = ApiAuthSettingsLoader({"HOLMES_DEFAULT_API_KEY_FILE": str(key_file)})
    assert loader.get().keys == {}

    key_file.write_text("first")
    assert loader.get().keys == {"HOLMES_DEFAULT_API_KEY_FILE": "first"}

    # Kubelet swaps a symlink; a rename gives the path a new inode the same way.
    replacement = tmp_path / "token.new"
    replacement.write_text("second-key")
    os.replace(replacement, key_file)
    assert loader.get().keys == {"HOLMES_DEFAULT_API_KEY_FILE": "second-key"}

    key_file.unlink()
    assert loader.get().keys == {}


def test_loader_keeps_user_key_while_the_file_changes(tmp_path):
    key_file = tmp_path / "token"
    loader = ApiAuthSettingsLoader(
        {"HOLMES_API_KEY": "mine", "HOLMES_DEFAULT_API_KEY_FILE": str(key_file)}
    )
    key_file.write_text("chart")
    settings = loader.get()
    assert is_valid_api_key("mine", settings)
    assert is_valid_api_key("chart", settings)
