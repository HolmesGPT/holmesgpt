"""Tests for the general-purpose Datadog API toolset."""

from typing import Optional
from unittest.mock import Mock, patch

from holmes.core.tools import StructuredToolResultStatus, Tool
from holmes.plugins.toolsets.datadog.toolset_datadog_general import (
    BLACKLISTED_SEGMENTS,
    DatadogGeneralToolset,
    EndpointTemplate,
    GET_ENDPOINT_TEMPLATES,
    POST_ENDPOINT_TEMPLATES,
    build_endpoint_from_template,
    get_valid_get_endpoint_templates,
    get_valid_post_endpoint_templates,
)
from tests.conftest import create_mock_tool_invoke_context


def get_tool_by_name(toolset: DatadogGeneralToolset, name: str) -> Optional[Tool]:
    """Get a tool from the toolset by its name."""
    for tool in toolset.tools:
        if tool.name == name:
            return tool
    return None


class TestEndpointTemplates:
    """Test endpoint template structure and validation."""

    def test_get_endpoint_templates_structure(self) -> None:
        """Test that GET endpoint templates have valid structure."""
        for template in GET_ENDPOINT_TEMPLATES:
            assert isinstance(template, EndpointTemplate)
            assert template.template.startswith("/api/v")
            assert template.description
            # If template has placeholder, placeholder_name should be set
            if "{" in template.template:
                assert template.placeholder_name is not None

    def test_post_endpoint_templates_structure(self) -> None:
        """Test that POST endpoint templates have valid structure."""
        for template in POST_ENDPOINT_TEMPLATES:
            assert isinstance(template, EndpointTemplate)
            assert template.template.startswith("/api/v")
            assert template.description

    def test_get_valid_get_endpoint_templates(self) -> None:
        """Test that get_valid_get_endpoint_templates returns list of strings."""
        templates = get_valid_get_endpoint_templates()
        assert isinstance(templates, list)
        assert len(templates) > 0
        assert all(isinstance(t, str) for t in templates)
        assert "/api/v1/monitor" in templates
        assert "/api/v1/monitor/{monitor_id}" in templates

    def test_get_valid_post_endpoint_templates(self) -> None:
        """Test that get_valid_post_endpoint_templates returns list of strings."""
        templates = get_valid_post_endpoint_templates()
        assert isinstance(templates, list)
        assert len(templates) > 0
        assert all(isinstance(t, str) for t in templates)
        assert "/api/v1/monitor/search" in templates


class TestBuildEndpointFromTemplate:
    """Test endpoint building from templates."""

    def test_valid_get_endpoint_without_placeholder(self) -> None:
        """Test building endpoint from template without placeholder."""
        success, endpoint, description = build_endpoint_from_template(
            "/api/v1/monitor", None, method="GET"
        )
        assert success is True
        assert endpoint == "/api/v1/monitor"
        assert "List all monitors" in description

    def test_valid_get_endpoint_with_placeholder(self) -> None:
        """Test building endpoint from template with placeholder."""
        success, endpoint, description = build_endpoint_from_template(
            "/api/v1/monitor/{monitor_id}", "12345", method="GET"
        )
        assert success is True
        assert endpoint == "/api/v1/monitor/12345"
        assert "Get a specific monitor" in description

    def test_missing_resource_id_for_placeholder(self) -> None:
        """Test error when resource_id is missing for template with placeholder."""
        success, error, _ = build_endpoint_from_template(
            "/api/v1/monitor/{monitor_id}", None, method="GET"
        )
        assert success is False
        assert "requires resource_id parameter" in error

    def test_extra_resource_id_for_template_without_placeholder(self) -> None:
        """Test that extra resource_id is ignored for template without placeholder."""
        success, endpoint, _ = build_endpoint_from_template(
            "/api/v1/monitor", "ignored", method="GET"
        )
        # Should succeed but ignore the resource_id
        assert success is True
        assert endpoint == "/api/v1/monitor"

    def test_invalid_template(self) -> None:
        """Test error for invalid template."""
        success, error, _ = build_endpoint_from_template(
            "/api/v1/invalid/endpoint", None, method="GET"
        )
        assert success is False
        assert "Invalid endpoint_template" in error

    def test_valid_post_endpoint(self) -> None:
        """Test building POST endpoint from template."""
        success, endpoint, description = build_endpoint_from_template(
            "/api/v1/monitor/search", None, method="POST"
        )
        assert success is True
        assert endpoint == "/api/v1/monitor/search"
        assert "Search monitors" in description

    def test_blacklisted_segment_in_resource_id(self) -> None:
        """Test that blacklisted segments in resource_id are caught."""
        # This would produce /api/v1/hosts/delete which contains a blacklisted segment
        success, error, _ = build_endpoint_from_template(
            "/api/v1/hosts/{hostname}", "delete", method="GET"
        )
        assert success is False
        assert "blacklisted operation" in error

    def test_various_placeholder_substitutions(self) -> None:
        """Test placeholder substitution for various endpoint types."""
        test_cases = [
            ("/api/v1/dashboard/{dashboard_id}", "abc-123", "/api/v1/dashboard/abc-123"),
            ("/api/v1/slo/{slo_id}", "slo-456", "/api/v1/slo/slo-456"),
            ("/api/v2/incidents/{incident_id}", "INC-789", "/api/v2/incidents/INC-789"),
        ]

        for template, resource_id, expected_endpoint in test_cases:
            success, endpoint, _ = build_endpoint_from_template(
                template, resource_id, method="GET"
            )
            assert success is True, f"Failed for template: {template}"
            assert endpoint == expected_endpoint, f"Expected {expected_endpoint}, got {endpoint}"

    def test_path_traversal_blocked(self) -> None:
        """Test that path traversal attempts in resource_id are blocked."""
        traversal_attempts = [
            "../../../etc/passwd",
            "..%2f..%2f..%2fetc/passwd",
            "foo/bar",
            "test%2fvalue",
            "..%2e%2e/secret",
        ]
        for attempt in traversal_attempts:
            success, error, _ = build_endpoint_from_template(
                "/api/v1/monitor/{monitor_id}", attempt, method="GET"
            )
            assert success is False, f"Path traversal should be blocked: {attempt}"
            assert "invalid characters" in error

    def test_backreference_injection_prevented(self) -> None:
        """Test that regex backreference sequences in resource_id are safely handled."""
        # These could cause issues with naive re.sub() string replacement
        backreference_attempts = [
            r"\1",
            r"\g<name>",
            r"\\1test",
        ]
        for attempt in backreference_attempts:
            success, endpoint, _ = build_endpoint_from_template(
                "/api/v1/monitor/{monitor_id}", attempt, method="GET"
            )
            # Should succeed and URL-encode the special characters
            assert success is True, f"Should handle backreference safely: {attempt}"
            # The endpoint should contain URL-encoded version, not raw backreference
            assert attempt not in endpoint or "%5C" in endpoint

    def test_special_characters_url_encoded(self) -> None:
        """Test that special characters in resource_id are URL-encoded."""
        success, endpoint, _ = build_endpoint_from_template(
            "/api/v1/hosts/{hostname}", "host with spaces", method="GET"
        )
        assert success is True
        assert "host%20with%20spaces" in endpoint
        assert " " not in endpoint


class TestDatadogGeneralToolset:
    """Test the Datadog general toolset."""

    def test_toolset_initialization(self) -> None:
        """Test toolset initializes correctly."""
        toolset = DatadogGeneralToolset()

        assert toolset.name == "datadog/general"
        assert len(toolset.tools) == 3
        assert toolset.dd_config is None

        tool_names = [tool.name for tool in toolset.tools]
        assert "datadog_api_get" in tool_names
        assert "datadog_api_post_search" in tool_names
        assert "list_datadog_api_resources" in tool_names

    def test_datadog_api_get_has_enum_parameter(self) -> None:
        """Test that datadog_api_get has endpoint_template as enum."""
        toolset = DatadogGeneralToolset()
        get_tool = get_tool_by_name(toolset, "datadog_api_get")
        assert get_tool is not None

        assert "endpoint_template" in get_tool.parameters
        assert get_tool.parameters["endpoint_template"].enum is not None
        assert len(get_tool.parameters["endpoint_template"].enum) > 0
        assert "/api/v1/monitor" in get_tool.parameters["endpoint_template"].enum

    def test_datadog_api_post_search_has_enum_parameter(self) -> None:
        """Test that datadog_api_post_search has endpoint_template as enum."""
        toolset = DatadogGeneralToolset()
        post_tool = get_tool_by_name(toolset, "datadog_api_post_search")
        assert post_tool is not None

        assert "endpoint_template" in post_tool.parameters
        assert post_tool.parameters["endpoint_template"].enum is not None
        assert len(post_tool.parameters["endpoint_template"].enum) > 0
        assert "/api/v1/monitor/search" in post_tool.parameters["endpoint_template"].enum

    def test_list_api_resources_tool(self) -> None:
        """Test the list API resources tool."""
        toolset = DatadogGeneralToolset()
        list_tool = get_tool_by_name(toolset, "list_datadog_api_resources")
        assert list_tool is not None

        # Test listing all resources
        result = list_tool._invoke({}, context=create_mock_tool_invoke_context())
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "monitor" in result.data.lower()
        assert "dashboard" in result.data.lower()
        # Check for the new format
        assert "GET Endpoints:" in result.data
        assert "POST Endpoints" in result.data
        assert "/api/v1/monitor" in result.data

    def test_list_api_resources_with_filter(self) -> None:
        """Test the list API resources tool with regex filter."""
        toolset = DatadogGeneralToolset()
        list_tool = get_tool_by_name(toolset, "list_datadog_api_resources")
        assert list_tool is not None

        result = list_tool._invoke(
            {"search_regex": "monitor"},
            context=create_mock_tool_invoke_context()
        )
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "monitor" in result.data.lower()
        # Should not include non-matching endpoints
        assert "synthetics" not in result.data.lower()

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_general.execute_datadog_http_request"
    )
    @patch("holmes.plugins.toolsets.datadog.toolset_datadog_general.get_headers")
    def test_api_get_tool_with_new_parameters(self, mock_headers, mock_execute) -> None:
        """Test the API GET tool with new endpoint_template parameter."""
        toolset = DatadogGeneralToolset()
        toolset.dd_config = Mock()
        toolset.dd_config.api_url = "https://api.datadoghq.com"
        toolset.dd_config.max_response_size = 10485760
        toolset.dd_config.allow_custom_endpoints = False
        toolset.dd_config.timeout_seconds = 60

        get_tool = get_tool_by_name(toolset, "datadog_api_get")
        assert get_tool is not None

        mock_headers.return_value = {"DD-API-KEY": "test", "DD-APPLICATION-KEY": "test"}
        mock_execute.return_value = {"data": "test_response"}

        # Test valid endpoint template without placeholder
        result = get_tool._invoke(
            {
                "endpoint_template": "/api/v1/monitor",
                "query_params": {"limit": 10},
                "description": "List monitors",
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "test_response" in result.data

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_general.execute_datadog_http_request"
    )
    @patch("holmes.plugins.toolsets.datadog.toolset_datadog_general.get_headers")
    def test_api_get_tool_with_resource_id(self, mock_headers, mock_execute) -> None:
        """Test the API GET tool with endpoint_template and resource_id."""
        toolset = DatadogGeneralToolset()
        toolset.dd_config = Mock()
        toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        toolset.dd_config.max_response_size = 10485760
        toolset.dd_config.allow_custom_endpoints = False
        toolset.dd_config.request_timeout = 60

        get_tool = get_tool_by_name(toolset, "datadog_api_get")
        assert get_tool is not None

        mock_headers.return_value = {"DD-API-KEY": "test", "DD-APPLICATION-KEY": "test"}
        mock_execute.return_value = {"data": "monitor_details"}

        # Test valid endpoint template with placeholder
        result = get_tool._invoke(
            {
                "endpoint_template": "/api/v1/monitor/{monitor_id}",
                "resource_id": "12345",
                "description": "Get monitor details",
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "monitor_details" in result.data
        # Verify the correct URL was called
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args
        assert "12345" in call_args.kwargs["url"]

    def test_api_get_tool_missing_resource_id(self) -> None:
        """Test the API GET tool returns error when resource_id is missing."""
        toolset = DatadogGeneralToolset()
        toolset.dd_config = Mock()
        toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        toolset.dd_config.max_response_size = 10485760
        toolset.dd_config.allow_custom_endpoints = False

        get_tool = get_tool_by_name(toolset, "datadog_api_get")
        assert get_tool is not None

        result = get_tool._invoke(
            {
                "endpoint_template": "/api/v1/monitor/{monitor_id}",
                "description": "Get monitor",
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "requires resource_id" in result.error

    def test_api_get_tool_invalid_template(self) -> None:
        """Test the API GET tool returns error for invalid template."""
        toolset = DatadogGeneralToolset()
        toolset.dd_config = Mock()
        toolset.dd_config.site_api_url = "https://api.datadoghq.com"

        get_tool = get_tool_by_name(toolset, "datadog_api_get")
        assert get_tool is not None

        result = get_tool._invoke(
            {
                "endpoint_template": "/api/v1/invalid/endpoint",
                "description": "Invalid endpoint",
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid endpoint_template" in result.error

    @patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_general.execute_datadog_http_request"
    )
    @patch("holmes.plugins.toolsets.datadog.toolset_datadog_general.get_headers")
    def test_api_post_search_tool(self, mock_headers, mock_execute) -> None:
        """Test the API POST search tool with new endpoint_template parameter."""
        toolset = DatadogGeneralToolset()
        toolset.dd_config = Mock()
        toolset.dd_config.site_api_url = "https://api.datadoghq.com"
        toolset.dd_config.max_response_size = 10485760
        toolset.dd_config.allow_custom_endpoints = False
        toolset.dd_config.request_timeout = 60

        post_tool = get_tool_by_name(toolset, "datadog_api_post_search")
        assert post_tool is not None

        mock_headers.return_value = {"DD-API-KEY": "test", "DD-APPLICATION-KEY": "test"}
        mock_execute.return_value = {"monitors": [{"id": 1}]}

        result = post_tool._invoke(
            {
                "endpoint_template": "/api/v1/monitor/search",
                "body": {"query": "env:prod"},
                "description": "Search monitors",
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert "monitors" in result.data

    def test_api_post_search_tool_invalid_template(self) -> None:
        """Test the API POST search tool returns error for invalid template."""
        toolset = DatadogGeneralToolset()
        toolset.dd_config = Mock()
        toolset.dd_config.site_api_url = "https://api.datadoghq.com"

        post_tool = get_tool_by_name(toolset, "datadog_api_post_search")
        assert post_tool is not None

        result = post_tool._invoke(
            {
                "endpoint_template": "/api/v1/invalid/search",
                "body": {},
                "description": "Invalid search",
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.ERROR
        assert "Invalid endpoint_template" in result.error


class TestBlacklistedSegments:
    """Test blacklisted segment protection."""

    def test_blacklisted_segments_exist(self) -> None:
        """Test that blacklisted segments list is populated."""
        assert len(BLACKLISTED_SEGMENTS) > 0
        assert "/create" in BLACKLISTED_SEGMENTS
        assert "/delete" in BLACKLISTED_SEGMENTS
        assert "/update" in BLACKLISTED_SEGMENTS

    def test_blacklisted_segment_in_resource_id_blocked(self) -> None:
        """Test that blacklisted segments in resource_id are blocked."""
        # Try various blacklisted segments
        for segment in ["/create", "/delete", "/update", "/mute"]:
            resource_id = segment.lstrip("/")  # e.g., "create", "delete"
            success, error, _ = build_endpoint_from_template(
                "/api/v1/hosts/{hostname}", resource_id, method="GET"
            )
            assert success is False, f"Expected {segment} to be blocked"
            assert "blacklisted operation" in error
