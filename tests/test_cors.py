import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_cors():
    """Create a test client with CORS enabled."""
    with patch.dict(
        "os.environ",
        {
            "CORS_ENABLED": "true",
            "CORS_ALLOW_ORIGINS": "http://localhost:3000,https://example.com",
            "CORS_ALLOW_CREDENTIALS": "true",
            "CORS_ALLOW_METHODS": "GET,POST,OPTIONS",
            "CORS_ALLOW_HEADERS": "Content-Type,Authorization",
        },
    ):
        # Need to reload the modules to pick up the new env vars
        import importlib
        import holmes.common.env_vars

        importlib.reload(holmes.common.env_vars)

        # Re-import server to get fresh app with CORS middleware
        import server

        importlib.reload(server)

        yield TestClient(server.app)


@pytest.fixture
def client_without_cors():
    """Create a test client with CORS disabled (default)."""
    with patch.dict(
        "os.environ",
        {
            "CORS_ENABLED": "false",
        },
    ):
        import importlib
        import holmes.common.env_vars

        importlib.reload(holmes.common.env_vars)

        import server

        importlib.reload(server)

        yield TestClient(server.app)


def test_cors_preflight_request_when_enabled(client_with_cors):
    """Test that CORS preflight requests work when CORS is enabled."""
    response = client_with_cors.options(
        "/api/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,Authorization",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_headers_on_response_when_enabled(client_with_cors):
    """Test that CORS headers are included in responses when CORS is enabled."""
    response = client_with_cors.get(
        "/healthz",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_unlisted_origin(client_with_cors):
    """Test that origins not in the allowed list don't get CORS headers."""
    response = client_with_cors.get(
        "/healthz",
        headers={"Origin": "http://malicious-site.com"},
    )
    assert response.status_code == 200
    # The origin should not be reflected back if it's not in the allowed list
    assert response.headers.get("access-control-allow-origin") != "http://malicious-site.com"


def test_cors_headers_not_present_when_disabled(client_without_cors):
    """Test that CORS headers are not present when CORS is disabled."""
    response = client_without_cors.get(
        "/healthz",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


class TestCorsEnvVarParsing:
    """Test that CORS environment variables are parsed correctly."""

    def test_parse_origins_single(self):
        """Test parsing a single origin."""
        with patch.dict("os.environ", {"CORS_ALLOW_ORIGINS": "http://localhost:3000"}):
            import importlib
            import holmes.common.env_vars

            importlib.reload(holmes.common.env_vars)

            origins = [
                o.strip()
                for o in holmes.common.env_vars.CORS_ALLOW_ORIGINS.split(",")
                if o.strip()
            ]
            assert origins == ["http://localhost:3000"]

    def test_parse_origins_multiple(self):
        """Test parsing multiple origins."""
        with patch.dict(
            "os.environ",
            {"CORS_ALLOW_ORIGINS": "http://localhost:3000, https://example.com , https://api.example.com"},
        ):
            import importlib
            import holmes.common.env_vars

            importlib.reload(holmes.common.env_vars)

            origins = [
                o.strip()
                for o in holmes.common.env_vars.CORS_ALLOW_ORIGINS.split(",")
                if o.strip()
            ]
            assert origins == [
                "http://localhost:3000",
                "https://example.com",
                "https://api.example.com",
            ]

    def test_cors_enabled_default_is_false(self):
        """Test that CORS is disabled by default."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            import holmes.common.env_vars

            # Clear CORS_ENABLED from env if it exists
            import os
            os.environ.pop("CORS_ENABLED", None)

            importlib.reload(holmes.common.env_vars)

            assert holmes.common.env_vars.CORS_ENABLED is False

    def test_cors_enabled_true(self):
        """Test that CORS can be enabled via environment variable."""
        with patch.dict("os.environ", {"CORS_ENABLED": "true"}):
            import importlib
            import holmes.common.env_vars

            importlib.reload(holmes.common.env_vars)

            assert holmes.common.env_vars.CORS_ENABLED is True


class TestCorsWildcardCredentials:
    """Test that wildcard origin with credentials is handled correctly."""

    @pytest.fixture
    def client_with_wildcard_and_credentials(self):
        """Create a test client with wildcard origin and credentials enabled."""
        with patch.dict(
            "os.environ",
            {
                "CORS_ENABLED": "true",
                "CORS_ALLOW_ORIGINS": "*",
                "CORS_ALLOW_CREDENTIALS": "true",
                "CORS_ALLOW_METHODS": "*",
                "CORS_ALLOW_HEADERS": "*",
            },
        ):
            import importlib
            import holmes.common.env_vars

            importlib.reload(holmes.common.env_vars)

            import server

            importlib.reload(server)

            yield TestClient(server.app)

    def test_wildcard_origin_still_works(self, client_with_wildcard_and_credentials):
        """Test that CORS works with wildcard origin (credentials auto-disabled)."""
        response = client_with_wildcard_and_credentials.get(
            "/healthz",
            headers={"Origin": "http://any-origin.com"},
        )
        assert response.status_code == 200
        # Wildcard should allow any origin
        assert "access-control-allow-origin" in response.headers
        # With wildcard, the origin header should be "*"
        assert response.headers["access-control-allow-origin"] == "*"

    def test_wildcard_credentials_header_not_present(self, client_with_wildcard_and_credentials):
        """Test that credentials header is not present when auto-disabled."""
        response = client_with_wildcard_and_credentials.options(
            "/api/chat",
            headers={
                "Origin": "http://any-origin.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200
        # When credentials are disabled, this header should be absent or "false"
        # FastAPI's CORSMiddleware doesn't include the header when credentials=False
        credentials_header = response.headers.get("access-control-allow-credentials")
        assert credentials_header is None or credentials_header == "false"
