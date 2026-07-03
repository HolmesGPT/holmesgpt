from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.datastructures import URL

from holmes.utils.auth import AUTH_EXEMPT_PATHS, extract_api_key

TEST_API_KEY = "test-secret-key-12345"


def _create_app(api_key: str = ""):
    """Create a minimal FastAPI app with the same auth middleware as server.py."""
    app = FastAPI()

    if api_key:

        @app.middleware("http")
        async def api_key_auth(request: Request, call_next):
            if request.scope.get("path", "") in AUTH_EXEMPT_PATHS:
                return await call_next(request)

            key = extract_api_key(request)

            if key != api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                )
            return await call_next(request)

    @app.get("/healthz")
    def healthz():
        return {"status": "healthy"}

    @app.get("/readyz")
    def readyz():
        return {"status": "ready"}

    @app.post("/api/chat")
    def chat():
        return {"analysis": "ok"}

    @app.get("/api/model")
    def model():
        return {"model_name": ["test-model"]}

    return app


class TestAuthDisabled:
    """Verify that all endpoints are open when HOLMES_API_KEY is not set."""

    def setup_method(self):
        self.client = TestClient(_create_app(api_key=""))

    def test_request_without_key_succeeds(self):
        response = self.client.post("/api/chat")
        assert response.status_code == 200

    def test_healthz_succeeds(self):
        response = self.client.get("/healthz")
        assert response.status_code == 200

    def test_request_with_key_still_succeeds(self):
        response = self.client.post("/api/chat", headers={"X-API-Key": "anything"})
        assert response.status_code == 200


class TestAuthEnabled:
    """Verify key enforcement, header variants, and health-check exemptions."""

    def setup_method(self):
        self.client = TestClient(_create_app(api_key=TEST_API_KEY))

    def test_no_key_returns_401(self):
        response = self.client.post("/api/chat")
        assert response.status_code == 401
        assert "Invalid or missing API key" in response.json()["detail"]

    def test_wrong_key_returns_401(self):
        response = self.client.post("/api/chat", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401

    def test_valid_x_api_key_header(self):
        response = self.client.post("/api/chat", headers={"X-API-Key": TEST_API_KEY})
        assert response.status_code == 200
        assert response.json()["analysis"] == "ok"

    def test_valid_bearer_token(self):
        response = self.client.post(
            "/api/chat", headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        assert response.status_code == 200

    def test_healthz_exempt_without_key(self):
        response = self.client.get("/healthz")
        assert response.status_code == 200

    def test_readyz_exempt_without_key(self):
        response = self.client.get("/readyz")
        assert response.status_code == 200

    def test_get_endpoint_also_protected(self):
        response = self.client.get("/api/model")
        assert response.status_code == 401

    def test_get_endpoint_with_key(self):
        response = self.client.get("/api/model", headers={"X-API-Key": TEST_API_KEY})
        assert response.status_code == 200


class TestAuthBypassRegression:
    """Regression for CVE-2026-48710 / GHSA-86qp-5c8j-p5mr.

    The vulnerability allowed an attacker to send a malformed Host header so
    that ``request.url.path`` (reconstructed from the header) differed from
    the raw ASGI ``scope['path']`` that the router actually dispatched on.
    Middleware that gated security exemptions on ``request.url.path`` could
    therefore be bypassed: the attacker hits a protected route, but the
    exemption check sees a forged ``/healthz`` (or ``/readyz``) and lets the
    request through unauthenticated.

    The fix is to read the raw scope path in the middleware. This test
    simulates the spoof by overriding ``Request.url`` to always return
    ``/healthz``; with the old code it would reach the protected endpoint
    with status 200, with the fix it must return 401.
    """

    def test_spoofed_url_path_does_not_bypass_auth(self, monkeypatch):
        fake_url = URL("http://attacker.example/healthz")
        monkeypatch.setattr(
            "starlette.requests.Request.url",
            property(lambda self: fake_url),
        )

        client = TestClient(_create_app(api_key=TEST_API_KEY))

        response = client.post("/api/chat")

        assert response.status_code == 401, (
            "Auth exemption must be checked against request.scope['path'], "
            "not the reconstructed request.url.path (CVE-2026-48710)."
        )
