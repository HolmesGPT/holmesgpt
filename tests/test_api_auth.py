from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from holmes.utils.auth import AUTH_EXEMPT_PATHS, extract_api_key

TEST_API_KEY = "test-secret-key-12345"


def _create_app(api_key: str = ""):
    """Create a minimal FastAPI app with the same auth middleware as server.py."""
    app = FastAPI()

    if api_key:

        @app.middleware("http")
        async def api_key_auth(request: Request, call_next):
            if request.url.path in AUTH_EXEMPT_PATHS:
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
