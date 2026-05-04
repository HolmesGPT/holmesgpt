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
    @patch("holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPToolset.check_prerequisites")
    @patch("projects._fetch_secret")
    def test_atlassian_connection_success_via_secret_arn(
        self, mock_secret, mock_check
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
        mock_check.return_value = (True, "")

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"
        assert "tool_count" in body

    @patch.dict(os.environ, {"MCP_JENKINS_API_KEY": "env-key"}, clear=False)
    @patch("holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPToolset.check_prerequisites")
    def test_jenkins_connection_success_via_env_fallback(self, mock_check):
        from server_frontend import _test_mcp_instance_connection  # noqa: PLC0415
        from projects import Instance  # noqa: PLC0415

        inst = Instance(
            id="inst_jk1",
            type="jenkins",
            name="jenkins-test",
        )
        mock_check.return_value = (True, "")

        store = MagicMock()
        body = asyncio.run(_test_mcp_instance_connection(store, inst))
        assert body["ok"] is True
        assert body["status"] == "success"

    @patch("holmes.plugins.toolsets.mcp.toolset_mcp.RemoteMCPToolset.check_prerequisites")
    @patch("projects._fetch_secret")
    def test_atlassian_connection_strips_api_key_from_error(
        self, mock_secret, mock_check
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
        mock_check.return_value = (False, f"HTTP 401: token '{leaked_key}' rejected")

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
