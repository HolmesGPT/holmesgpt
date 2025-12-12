"""
Live tests for Datadog RDS toolset.

These tests require valid Datadog API credentials and access to RDS instances.
Set the following environment variables:
- DD_API_KEY: Datadog API key
- DD_APP_KEY: Datadog Application key
- DD_SITE_API_URL: Datadog site API URL (e.g., https://api.datadoghq.com)
- DD_TEST_RDS_INSTANCE: RDS instance identifier to test with
"""

import os
import pytest

from holmes.plugins.toolsets.datadog.toolset_datadog_rds import DatadogRDSToolset
from holmes.core.tools import StructuredToolResultStatus
from tests.conftest import create_mock_tool_invoke_context


@pytest.fixture
def datadog_rds_config():
    """Get Datadog RDS configuration from environment variables."""
    api_key = os.getenv("DD_API_KEY")
    app_key = os.getenv("DD_APP_KEY")
    site_url = os.getenv("DD_SITE_URL", "https://api.datadoghq.com")

    if not api_key or not app_key:
        pytest.skip("Datadog API credentials not found in environment variables")

    return {
        "dd_api_key": api_key,
        "dd_app_key": app_key,
        "site_api_url": site_url,
        "request_timeout": 30,
    }


@pytest.fixture
def test_rds_instance():
    """Get test RDS instance identifier from environment."""
    instance = os.getenv("DD_TEST_RDS_INSTANCE", "demo-rds-postgres-prod")
    return instance


@pytest.fixture
def datadog_rds_toolset(datadog_rds_config):
    """Create and initialize Datadog RDS toolset."""
    toolset = DatadogRDSToolset()
    prereq = toolset.prerequisites_check(datadog_rds_config)
    success, message = prereq.callable(datadog_rds_config)

    if not success:
        pytest.skip(f"Prerequisites check failed: {message}")

    toolset.post_init(datadog_rds_config)
    return toolset


def test_generate_performance_report(datadog_rds_toolset, test_rds_instance):
    """Test generating RDS performance report."""
    # Get tool by name
    tools = {tool.name: tool for tool in datadog_rds_toolset.tools}
    tool = tools.get("datadog_rds_performance_report")
    assert tool is not None, "datadog_rds_performance_report tool not found"

    # Test with recent data (last hour)
    params = {
        "db_instance_identifier": test_rds_instance,
        "start_time": "-3600",  # 1 hour ago
    }

    result = tool._invoke(params, context=create_mock_tool_invoke_context())

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert test_rds_instance in result.data


def test_get_top_worst_performing_instances(datadog_rds_toolset, test_rds_instance):
    """Test getting top worst performing RDS instances."""
    # Get tool by name
    tools = {tool.name: tool for tool in datadog_rds_toolset.tools}
    tool = tools.get("datadog_rds_top_worst_performing")
    assert tool is not None, "datadog_rds_top_worst_performing tool not found"

    params = {
        "top_n": 5,
        "start_time": "-3600",
        "sort_by": "latency",
    }

    result = tool._invoke(params, context=create_mock_tool_invoke_context())

    assert result.status == StructuredToolResultStatus.SUCCESS

    assert test_rds_instance in result.data


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
