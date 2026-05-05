"""Unit tests for the Bitbucket Cloud toolset."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import ValidationError

from holmes.plugins.toolsets.bitbucket.toolset_bitbucket import (
    BitbucketAuthError,
    BitbucketConfig,
    BitbucketForbiddenError,
    BitbucketRateLimitError,
    BitbucketToolset,
)


class TestBitbucketConfig:
    def test_minimum_config(self):
        cfg = BitbucketConfig(api_token="t", workspace="acme")
        assert cfg.api_token == "t"
        assert cfg.workspace == "acme"
        assert cfg.repositories is None
        assert cfg.default_limit == 25
        assert cfg.api_url == "https://api.bitbucket.org/2.0"

    def test_with_repositories_allowlist(self):
        cfg = BitbucketConfig(
            api_token="t",
            workspace="acme",
            repositories=["checkout-api", "inventory-db"],
        )
        assert cfg.repositories == ["checkout-api", "inventory-db"]

    def test_missing_workspace_raises(self):
        with pytest.raises(ValidationError):
            BitbucketConfig(api_token="t")

    def test_api_url_override(self):
        cfg = BitbucketConfig(api_token="t", workspace="acme", api_url="http://localhost:9000")
        assert cfg.api_url == "http://localhost:9000"


class TestBitbucketExceptionsExist:
    """Verify the three custom exception classes are importable RuntimeError subclasses."""

    def test_auth_error(self):
        assert issubclass(BitbucketAuthError, RuntimeError)

    def test_forbidden_error(self):
        assert issubclass(BitbucketForbiddenError, RuntimeError)

    def test_rate_limit_error(self):
        assert issubclass(BitbucketRateLimitError, RuntimeError)


def _mock_resp(status_code: int, json_body=None, text="", headers=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_body or {}
    m.text = text
    m.headers = headers or {}
    m.raise_for_status = MagicMock()
    if status_code >= 400:
        m.raise_for_status.side_effect = requests.HTTPError(response=m)
    return m


class TestHealthCheck:
    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_200(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"slug": "acme"})
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "t", "workspace": "acme"})
        assert ok is True
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer t"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_401(self, mock_get):
        mock_get.return_value = _mock_resp(401, text="Unauthorized")
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "bad", "workspace": "acme"})
        assert ok is False
        assert "rejected" in msg.lower() or "401" in msg

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_403(self, mock_get):
        mock_get.return_value = _mock_resp(403, text="Forbidden")
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "t", "workspace": "acme"})
        assert ok is False
        assert "no access" in msg.lower() or "403" in msg

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_health_check_network_exception(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("connrefused")
        ts = BitbucketToolset()
        ok, msg = ts.prerequisites_callable({"api_token": "t", "workspace": "acme"})
        assert ok is False
        assert "failed" in msg.lower()


class TestGetHTTPWrapper:
    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_401_raises_auth_error(self, mock_get):
        mock_get.return_value = _mock_resp(401)
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(BitbucketAuthError):
            ts.get("/repositories/acme")

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_403_raises_forbidden_error(self, mock_get):
        mock_get.return_value = _mock_resp(403)
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(BitbucketForbiddenError):
            ts.get("/repositories/acme")

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_429_raises_rate_limit_with_retry_after(self, mock_get):
        mock_get.return_value = _mock_resp(429, headers={"Retry-After": "60"})
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(BitbucketRateLimitError) as exc_info:
            ts.get("/repositories/acme")
        assert "60" in str(exc_info.value)

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_404_bubbles_httperror(self, mock_get):
        mock_get.return_value = _mock_resp(404)
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        with pytest.raises(requests.HTTPError):
            ts.get("/repositories/acme/nope")

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_200_returns_json(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"slug": "x", "values": []})
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme")
        result = ts.get("/repositories/acme", params={"pagelen": 10})
        assert result == {"slug": "x", "values": []}
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"pagelen": 10}
        assert kwargs["headers"]["Authorization"] == "Bearer t"
