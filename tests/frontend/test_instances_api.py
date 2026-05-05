"""Integration tests for the /api/instances/{id}/test-connection endpoint helpers."""

import asyncio
import os
from unittest.mock import patch, MagicMock
import pytest


class TestPagerDutyConnectionHelper:
    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    @patch("projects._fetch_secret")
    def test_connection_success(self, mock_secret, mock_get):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-project-x",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-x",
            config={"service_ids": ["PSVC1"]},
        )
        mock_secret.return_value = {"api_key": "good-key"}

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"services": []}
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.run(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("holmes.plugins.toolsets.pagerduty.toolset_pagerduty.requests.get")
    @patch("projects._fetch_secret")
    def test_connection_401_returns_clear_error(self, mock_secret, mock_get):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-bad",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-bad",
        )
        mock_secret.return_value = {"api_key": "bad"}

        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.run(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is False
        assert body["status"] == "error"
        assert "invalid or expired" in body["error"]

    def test_connection_no_credential_source(self):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-empty",
        )
        store = MagicMock()
        body = asyncio.run(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is False
        assert "no credential source" in body["error"]

    @patch("projects._fetch_secret")
    def test_connection_secret_missing_api_key(self, mock_secret):
        from server_frontend import _test_pagerduty_instance_connection
        from projects import Instance

        inst = Instance(
            id="inst_pd1",
            type="pagerduty",
            name="pd-bad-secret",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:pd-bad",
        )
        mock_secret.return_value = {"some_other_key": "x"}  # no api_key
        store = MagicMock()
        body = asyncio.run(
            _test_pagerduty_instance_connection(store, inst)
        )
        assert body["ok"] is False
        assert "no 'api_key' field" in body["error"]


class TestJenkinsInMcpRegistry:
    def test_jenkins_registered_in_mcp_types(self):
        from projects import (  # noqa: PLC0415
            _MCP_DEFAULT_URLS,
            _MCP_DESCRIPTIONS,
            _MCP_ICONS,
            _MCP_TOOLSET_TYPES,
        )

        assert "jenkins" in _MCP_TOOLSET_TYPES
        assert _MCP_DEFAULT_URLS["jenkins"] == (
            "https://mcp-api.platform.pditechnologies.com/v1/jenkins-sse/mcp"
        )
        assert _MCP_ICONS["jenkins"].startswith("https://cdn.simpleicons.org/jenkins/")
        assert "Jenkins" in _MCP_DESCRIPTIONS["jenkins"]


class TestMcpConnectionHelper:
    """MCP toolsets' check_prerequisites() returns None and sets self.status
    and self.error. The helper reads those attributes. These tests patch
    projects._build_mcp_toolset to return a fake toolset with preset state.
    """

    @staticmethod
    def _fake_toolset(status="ENABLED", error="", tools=None):
        """Build a fake toolset MagicMock matching the interface the helper uses."""
        from holmes.core.tools import ToolsetStatusEnum  # noqa: PLC0415

        ts = MagicMock()
        ts.status = (
            ToolsetStatusEnum.ENABLED if status == "ENABLED" else ToolsetStatusEnum.FAILED
        )
        ts.error = error
        ts.tools = tools if tools is not None else [MagicMock(), MagicMock()]
        ts.check_prerequisites = MagicMock(return_value=None)
        return ts

    @patch("projects._build_mcp_toolset")
    @patch("projects._fetch_secret")
    def test_atlassian_connection_success_via_secret_arn(
        self, mock_secret, mock_build
    ):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_at1",
            type="atlassian",
            name="atlassian-test",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:at1",
        )
        mock_secret.return_value = {"api_key": "real-key"}
        mock_build.return_value = self._fake_toolset(status="ENABLED")

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"
        assert body["tool_count"] == 2

    @patch.dict(os.environ, {"MCP_JENKINS_API_KEY": "env-key"}, clear=False)
    @patch("projects._build_mcp_toolset")
    def test_jenkins_connection_success_via_env_fallback(self, mock_build):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_jk1",
            type="jenkins",
            name="jenkins-test",
        )
        mock_build.return_value = self._fake_toolset(status="ENABLED")

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("projects._build_mcp_toolset")
    @patch("projects._fetch_secret")
    def test_atlassian_connection_strips_api_key_from_error(
        self, mock_secret, mock_build
    ):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        leaked_key = "sk_live_SHOULD_NOT_LEAK_abc123"
        inst = Instance(
            id="inst_at2",
            type="atlassian",
            name="atlassian-bad",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:at2",
        )
        mock_secret.return_value = {"api_key": leaked_key}
        mock_build.return_value = self._fake_toolset(
            status="FAILED",
            error=f"HTTP 401: token '{leaked_key}' rejected",
        )

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is False
        assert body["status"] == "error"
        assert leaked_key not in body["error"]
        assert "<redacted>" in body["error"]

    def test_mcp_no_credential_source(self):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        # Ensure no env var is set for this test.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MCP_ATLASSIAN_API_KEY", None)
            inst = Instance(
                id="inst_at3",
                type="atlassian",
                name="atlassian-empty",
            )
            store = MagicMock()
            body = asyncio.run(_test_mcp_instance_connection(store, inst))
            assert body["ok"] is False
            assert body["status"] == "error"
            assert "No credential source" in body["error"]

    @patch("projects._build_mcp_toolset")
    @patch("projects._fetch_secret")
    def test_atlassian_connection_strips_url_encoded_api_key_from_error(
        self, mock_secret, mock_build
    ):
        """Verify that a URL-encoded api_key in an error message is also redacted."""
        import urllib.parse

        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        # Use a key with chars that URL-encoding transforms (/, +, =).
        leaked_key = "sk/live+DO/NOT=LEAK=xyz"
        encoded = urllib.parse.quote(leaked_key, safe="")

        inst = Instance(
            id="inst_at4",
            type="atlassian",
            name="atlassian-url-enc",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:at4",
        )
        mock_secret.return_value = {"api_key": leaked_key}
        # Simulate a library that echoes the URL-encoded form of the header value.
        mock_build.return_value = self._fake_toolset(
            status="FAILED",
            error=f"HTTP 401 — url: https://server/?x-api-key={encoded}",
        )

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is False
        assert leaked_key not in body["error"]
        assert encoded not in body["error"]
        assert "<redacted>" in body["error"]


class TestBitbucketConnectionHelper:
    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    @patch("projects._fetch_secret")
    def test_bitbucket_connection_success_via_secret_arn(
        self, mock_secret, mock_get
    ):
        from server_frontend import _test_bitbucket_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_bb1",
            type="bitbucket",
            name="bb-test",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:bb-test",
        )
        mock_secret.return_value = {"api_token": "t", "workspace": "acme"}

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"slug": "acme"}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.run(_test_bitbucket_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("holmes.plugins.toolsets.bitbucket.toolset_bitbucket.requests.get")
    @patch("projects._fetch_secret")
    def test_bitbucket_connection_403_returns_clear_error(
        self, mock_secret, mock_get
    ):
        from server_frontend import _test_bitbucket_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_bb2",
            type="bitbucket",
            name="bb-bad",
            secret_arn="arn:aws:secretsmanager:us-east-1:1:secret:bb-bad",
        )
        mock_secret.return_value = {"api_token": "t", "workspace": "acme"}

        resp = MagicMock()
        resp.status_code = 403
        resp.text = "Forbidden"
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        store = MagicMock()
        body = asyncio.run(_test_bitbucket_instance_connection(store, inst))
        assert body["ok"] is False
        assert "no access" in body["error"].lower() or "403" in body["error"]

    def test_bitbucket_no_credential_source(self):
        from server_frontend import _test_bitbucket_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(id="inst_bb3", type="bitbucket", name="bb-empty")
        store = MagicMock()
        body = asyncio.run(_test_bitbucket_instance_connection(store, inst))
        assert body["ok"] is False
        assert "credential source" in body["error"].lower() or "secret_arn" in body["error"]
