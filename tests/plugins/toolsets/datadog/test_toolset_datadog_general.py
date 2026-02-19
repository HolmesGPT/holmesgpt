"""Tests for the general-purpose Datadog API toolset."""

from unittest.mock import Mock, patch

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.datadog.toolset_datadog_general import (
    DatadogGeneralToolset,
    is_endpoint_allowed,
)
from tests.conftest import create_mock_tool_invoke_context


class TestEndpointValidation:
    """Test endpoint validation logic."""

    def test_whitelisted_get_endpoints(self):
        """Test that whitelisted GET endpoints are allowed."""
        allowed_endpoints = [
            "/api/v1/monitor",
            "/api/v2/dashboard/abc-123",
            "/api/v1/slo/search",
            "/api/v2/incidents/INC-123",
            "/api/v1/synthetics/tests",
            "/api/v2/security_monitoring/rules",
            "/api/v1/hosts",
            "/api/v1/events",
            "/api/v1/usage/summary",
        ]

        for endpoint in allowed_endpoints:
            allowed, error = is_endpoint_allowed(endpoint, method="GET")
            assert allowed, f"Endpoint {endpoint} should be allowed: {error}"

    def test_blacklisted_operations(self):
        """Test that blacklisted operations are blocked."""
        blocked_endpoints = [
            "/api/v1/monitor/create",
            "/api/v1/dashboard/delete",
            "/api/v1/slo/update",
            "/api/v2/incidents/bulk_delete",
            "/api/v1/monitors/mute",
            "/api/v1/hosts/disable",
        ]

        for endpoint in blocked_endpoints:
            allowed, error = is_endpoint_allowed(endpoint, method="GET")
            assert not allowed, f"Endpoint {endpoint} should be blocked"
            assert "blacklisted operation" in error

    def test_post_endpoints_restricted(self):
        """Test that only specific POST endpoints are allowed."""
        # Allowed POST endpoints (search operations)
        allowed_post = [
            "/api/v1/monitor/search",
            "/api/v2/incidents/search",
            "/api/v2/security_monitoring/signals/search",
        ]

        for endpoint in allowed_post:
            allowed, error = is_endpoint_allowed(endpoint, method="POST")
            assert allowed, f"POST endpoint {endpoint} should be allowed: {error}"

        # Blocked POST endpoints
        blocked_post = [
            "/api/v1/monitor",  # Creation endpoint
            "/api/v1/dashboard",  # Creation endpoint
            "/api/v1/events",  # Should be GET only
        ]

        for endpoint in blocked_post:
            allowed, error = is_endpoint_allowed(endpoint, method="POST")
            assert not allowed, f"POST endpoint {endpoint} should be blocked"

    def test_unsupported_methods(self):
        """Test that unsupported HTTP methods are blocked."""
        methods = ["PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

        for method in methods:
            allowed, error = is_endpoint_allowed("/api/v1/monitor", method=method)
            assert not allowed, f"Method {method} should not be allowed"
            assert f"HTTP method {method} not allowed" in error

    def test_custom_endpoints_with_flag(self):
        """Test custom endpoint handling with allow_custom flag."""
        custom_endpoint = "/api/v1/custom/endpoint"

        # Without allow_custom flag
        allowed, error = is_endpoint_allowed(
            custom_endpoint, method="GET", allow_custom=False
        )
        assert not allowed
        assert "not in whitelist" in error

        # With allow_custom flag (but still checks blacklist)
        allowed, error = is_endpoint_allowed(
            custom_endpoint, method="GET", allow_custom=True
        )
        assert allowed

        # Custom endpoint with blacklisted segment should still be blocked
        blocked_custom = "/api/v1/custom/delete"
        allowed, error = is_endpoint_allowed(
            blocked_custom, method="GET", allow_custom=True
        )
        assert not allowed
        assert "blacklisted operation" in error


class TestDatadogGeneralToolset:
    """Test the Datadog general toolset."""

    def test_toolset_initialization(self):
        """Test toolset initializes correctly."""
        toolset = DatadogGeneralToolset()

        assert toolset.name == "datadog/general"
        assert len(toolset.tools) == 3
        assert toolset.dd_config is None

        tool_names = [tool.name for tool in toolset.tools]
        assert "datadog_api_get" in tool_names
        assert "datadog_api_post_search" in tool_names
        assert "list_datadog_api_resources" in tool_names

    def test_list_api_resources_tool(self):
        """Test the list API resources tool."""
        toolset = DatadogGeneralToolset()
        list_tool = toolset.tools[2]  # ListDatadogAPIResources

        # Test listing all resources
        result = list_tool._invoke({}, context=create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "monitor" in result.data.lower()
        assert "dashboard" in result.data.lower()
        assert "POST     /api/v1/monitor" in result.data

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_general.execute_datadog_http_request"
    )
    @patch("holmes.plugins.toolsets.datadog.toolset_datadog_general.get_headers")
    def test_api_get_tool(self, mock_headers, mock_execute):
        """Test the API GET tool."""
        toolset = DatadogGeneralToolset()
        toolset.dd_config = Mock()
        toolset.dd_config.api_url = "https://api.datadoghq.com"
        toolset.dd_config.max_response_size = 10485760
        toolset.dd_config.allow_custom_endpoints = False
        toolset.dd_config.timeout_seconds = 60

        get_tool = toolset.tools[0]  # DatadogAPIGet

        mock_headers.return_value = {"DD-API-KEY": "test", "DD-APPLICATION-KEY": "test"}
        mock_execute.return_value = {"data": "test_response"}

        # Test valid endpoint
        result = get_tool._invoke(
            {
                "endpoint": "/api/v1/monitor",
                "query_params": {"limit": 10},
                "description": "List monitors",
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "test_response" in result.data

        # Test blocked endpoint
        result = get_tool._invoke(
            {"endpoint": "/api/v1/monitor/create", "description": "Create monitor"},
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "blacklisted operation" in result.error
