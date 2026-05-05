"""Unit tests for the Bitbucket Cloud toolset."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import ValidationError

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.bitbucket.toolset_bitbucket import (
    BitbucketAuthError,
    BitbucketConfig,
    BitbucketForbiddenError,
    BitbucketRateLimitError,
    BitbucketToolset,
)
from tests.conftest import create_mock_tool_invoke_context


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


class TestCheckRepoInScope:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    def test_no_allowlist_any_repo_allowed(self):
        ts = self._toolset()
        assert ts._check_repo_in_scope("anything", {}) is None

    def test_allowlist_in_scope(self):
        ts = self._toolset(repositories=["a", "b"])
        assert ts._check_repo_in_scope("a", {}) is None

    def test_allowlist_out_of_scope_returns_error(self):
        ts = self._toolset(repositories=["a"])
        result = ts._check_repo_in_scope("b", {"repo_slug": "b"})
        assert result is not None
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error
        assert "['a']" in result.error

    def test_allowlist_case_insensitive(self):
        ts = self._toolset(repositories=["Checkout-API"])
        assert ts._check_repo_in_scope("checkout-api", {}) is None


class TestTruncate:
    def test_under_limit_unchanged(self):
        ts = BitbucketToolset()
        result = ts._truncate("small", max_bytes=100)
        assert result == "small"

    def test_byte_based_trim(self):
        ts = BitbucketToolset()
        payload = "a" * 500 + "\n" + "b" * 500
        result = ts._truncate(payload, max_bytes=600)
        assert "truncated" in result
        assert len(result.encode("utf-8")) <= 800  # rough bound: marker adds overhead

    def test_line_based_trim(self):
        ts = BitbucketToolset()
        payload = "\n".join(f"line {i}" for i in range(5000))
        result = ts._truncate(payload, max_bytes=0, line_mode=True, max_lines=2000)
        assert "truncated" in result
        assert result.count("\n") <= 2001  # 2000 lines + marker line


class TestInputValidation:
    def test_valid_repo_slug(self):
        ts = BitbucketToolset()
        assert ts._validate_repo_slug("checkout-api") is True
        assert ts._validate_repo_slug("a.b_c-d123") is True

    def test_invalid_repo_slug_rejects_path_traversal(self):
        ts = BitbucketToolset()
        assert ts._validate_repo_slug("foo/../bar") is False
        assert ts._validate_repo_slug("foo/bar") is False
        assert ts._validate_repo_slug("UPPER") is False

    def test_valid_ref(self):
        ts = BitbucketToolset()
        assert ts._validate_ref("main") is True
        assert ts._validate_ref("feature/new-thing") is True
        assert ts._validate_ref("v1.2.3") is True

    def test_invalid_ref_rejects_double_dot(self):
        ts = BitbucketToolset()
        assert ts._validate_ref("foo/../etc") is False
        assert ts._validate_ref("..") is False

    def test_validate_repo_slug_rejects_trailing_newline(self):
        ts = BitbucketToolset()
        assert ts._validate_repo_slug("foo\n") is False

    def test_validate_ref_rejects_trailing_newline(self):
        ts = BitbucketToolset()
        assert ts._validate_ref("main\n") is False

    def test_validate_repo_slug_rejects_standalone_dotdot(self):
        ts = BitbucketToolset()
        assert ts._validate_repo_slug("..") is False
        assert ts._validate_repo_slug(".") is True  # '.' alone is a weird but technically valid char set; double-dot is what matters


class TestRepoTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_repositories_hits_correct_url(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": [{"slug": "checkout-api"}]})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_repositories")
        result = tool._invoke({}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme" in args[0]

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_repositories_caps_pagelen_at_100(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_repositories")
        tool._invoke({"limit": 500}, create_mock_tool_invoke_context())
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["pagelen"] == 100

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_repository_in_scope(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"slug": "checkout-api", "mainbranch": {"name": "main"}})
        ts = self._toolset(repositories=["checkout-api"])
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_repository")
        result = tool._invoke({"repo_slug": "checkout-api"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.SUCCESS

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_repository_out_of_scope_blocks_before_api_call(self, mock_get):
        ts = self._toolset(repositories=["a"])
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_repository")
        result = tool._invoke({"repo_slug": "b"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error
        assert mock_get.call_count == 0

    def test_get_repository_invalid_slug_rejected(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_repository")
        result = tool._invoke({"repo_slug": "foo/../bar"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid repo_slug" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_repository_404_friendly_error(self, mock_get):
        mock_get.return_value = _mock_resp(404)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_repository")
        result = tool._invoke({"repo_slug": "nonexistent"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not found" in result.error.lower()
        assert "acme/nonexistent" in result.error
