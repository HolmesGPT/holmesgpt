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

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_basic_auth_when_email_set(self, mock_get):
        """App-password auth: when `email` is set, use Basic auth with base64(email:token)."""
        import base64

        mock_get.return_value = _mock_resp(200, {})
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(
            api_token="app-pass-xyz", workspace="acme", email="user@pdi.com"
        )
        ts.get("/repositories/acme")
        _, kwargs = mock_get.call_args
        expected = "Basic " + base64.b64encode(b"user@pdi.com:app-pass-xyz").decode()
        assert kwargs["headers"]["Authorization"] == expected


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


class TestPullRequestTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_prs_default_state_open(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_requests")
        tool._invoke({"repo_slug": "x"}, create_mock_tool_invoke_context())
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["state"] == "OPEN"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_prs_state_override(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_requests")
        tool._invoke(
            {"repo_slug": "x", "state": "MERGED"},
            create_mock_tool_invoke_context(),
        )
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["state"] == "MERGED"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_prs_out_of_scope_blocks(self, mock_get):
        ts = self._toolset(repositories=["a"])
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_requests")
        result = tool._invoke({"repo_slug": "b"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert mock_get.call_count == 0

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_pr(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"id": 42, "title": "Fix"})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/pullrequests/42" in args[0]

    def test_get_pr_missing_id(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request")
        result = tool._invoke(
            {"repo_slug": "x"}, create_mock_tool_invoke_context()
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "pull_request_id is required" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_pr_comments(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": [{"content": {"raw": "nit"}}]})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_request_comments")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/pullrequests/42/comments" in args[0]

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_prs_state_none_falls_back_to_open(self, mock_get):
        # LLM may emit {"state": null}; must behave like missing key, not send "None".
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_requests")
        tool._invoke({"repo_slug": "x", "state": None}, create_mock_tool_invoke_context())
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["state"] == "OPEN"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_pr_404_friendly_error(self, mock_get):
        mock_get.return_value = _mock_resp(404)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "999"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not found" in result.error.lower()
        assert "x#999" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_pr_out_of_scope_blocks(self, mock_get):
        ts = self._toolset(repositories=["a"])
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request")
        result = tool._invoke(
            {"repo_slug": "b", "pull_request_id": "1"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not in this project's scope" in result.error
        assert mock_get.call_count == 0

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_pr_comments_out_of_scope_blocks(self, mock_get):
        ts = self._toolset(repositories=["a"])
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_pull_request_comments")
        result = tool._invoke(
            {"repo_slug": "b", "pull_request_id": "1"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert mock_get.call_count == 0

    def test_get_pr_pull_request_id_none_rejected(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": None},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "pull_request_id is required" in result.error


class TestDiffTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_pr_diff_small_not_truncated(self, mock_get):
        mock_get.return_value = _mock_resp(200, text="diff --git a/x b/x\n+1 line")
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request_diff")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" not in result.data

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_pr_diff_large_truncated(self, mock_get):
        # 500 KB payload, default cap is 200 KB.
        big = "a" * 500_000
        mock_get.return_value = _mock_resp(200, text=big)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request_diff")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" in result.data
        assert len(result.data.encode("utf-8")) < 250_000

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_commit_diff_hits_correct_url(self, mock_get):
        mock_get.return_value = _mock_resp(200, text="diff ...")
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_commit_diff")
        result = tool._invoke(
            {"repo_slug": "x", "commit_sha": "abc123"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/diff/abc123" in args[0]

    def test_commit_diff_missing_sha(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_commit_diff")
        result = tool._invoke({"repo_slug": "x"}, create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.ERROR
        assert "commit_sha is required" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_commit_diff_404_friendly_error(self, mock_get):
        mock_get.return_value = _mock_resp(404)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_commit_diff")
        result = tool._invoke(
            {"repo_slug": "x", "commit_sha": "deadbeef"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "not found" in result.error.lower()
        assert "x@deadbeef" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_pr_diff_max_bytes_override_respected(self, mock_get):
        # 300 KB payload with max_bytes=500000 should NOT be truncated.
        big = "a" * 300_000
        mock_get.return_value = _mock_resp(200, text=big)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_pull_request_diff")
        result = tool._invoke(
            {"repo_slug": "x", "pull_request_id": "42", "max_bytes": 500_000},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" not in result.data
        assert len(result.data) == 300_000


class TestCommitTools:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_list_commits_default_branch(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"values": []})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_commits")
        result = tool._invoke(
            {"repo_slug": "x", "branch": "main"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/commits/main" in args[0]

    def test_list_commits_invalid_branch_rejected(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "list_bitbucket_commits")
        result = tool._invoke(
            {"repo_slug": "x", "branch": "main/../evil"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid" in result.error

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_get_commit(self, mock_get):
        mock_get.return_value = _mock_resp(200, {"hash": "abc123", "message": "Fix"})
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_commit")
        result = tool._invoke(
            {"repo_slug": "x", "commit_sha": "abc123"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/commit/abc123" in args[0]


class TestFileContentsTool:
    def _toolset(self, **cfg_kwargs):
        ts = BitbucketToolset()
        ts.bb_config = BitbucketConfig(api_token="t", workspace="acme", **cfg_kwargs)
        return ts

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_file_contents_small(self, mock_get):
        mock_get.return_value = _mock_resp(200, text="print('hello')\n")
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main", "path": "src/app.py"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" not in result.data
        args, _ = mock_get.call_args
        assert "/repositories/acme/x/src/main/src/app.py" in args[0]

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    def test_file_contents_large_truncated_at_2000_lines(self, mock_get):
        big = "\n".join(f"line {i}" for i in range(5000))
        mock_get.return_value = _mock_resp(200, text=big)
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main", "path": "big.txt"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "truncated" in result.data
        # Expect 2000 kept lines + 1 marker line
        assert result.data.count("\n") <= 2001

    def test_file_contents_invalid_ref_rejected(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main/../evil", "path": "x"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid ref" in result.error

    def test_file_contents_missing_path(self):
        ts = self._toolset()
        tool = next(t for t in ts.tools if t.name == "get_bitbucket_file_contents")
        result = tool._invoke(
            {"repo_slug": "x", "ref": "main"},
            create_mock_tool_invoke_context(),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "path is required" in result.error


class TestFactoryRegistration:
    def test_bitbucket_in_python_toolset_factories(self):
        from holmes.plugins.toolsets import PYTHON_TOOLSET_FACTORIES

        assert "bitbucket" in PYTHON_TOOLSET_FACTORIES
        assert PYTHON_TOOLSET_FACTORIES["bitbucket"] is BitbucketToolset
