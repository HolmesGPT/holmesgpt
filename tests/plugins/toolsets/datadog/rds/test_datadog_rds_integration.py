"""
Integration tests for Datadog RDS toolset.

These tests use mocked responses to test the toolset functionality
without requiring actual Datadog API access.
"""

import json
from unittest.mock import patch
import pytest
from datetime import datetime, timezone

from holmes.plugins.toolsets.datadog.toolset_datadog_rds import (
    DatadogRDSToolset,
    DatadogRDSConfig,
)
from holmes.plugins.toolsets.datadog.datadog_api import DataDogRequestError
from holmes.core.tools import StructuredToolResultStatus
from tests.conftest import create_mock_tool_invoke_context


@pytest.fixture
def mock_config():
    """Mock Datadog RDS configuration."""
    return {
        "dd_api_key": "test_api_key",
        "dd_app_key": "test_app_key",
        "site_api_url": "https://api.datadoghq.com",
        "request_timeout": 30,
    }


@pytest.fixture
def datadog_rds_toolset(mock_config):
    """Create Datadog RDS toolset with mocked prerequisites."""
    toolset = DatadogRDSToolset()

    # Directly set the config without going through prerequisites
    toolset.dd_config = DatadogRDSConfig(**mock_config)
    toolset.post_init(mock_config)

    return toolset


def create_mock_metric_response(
    metric_name, values, unit="ms", instance_id="test-instance"
):
    """Helper to create mock Datadog metric response."""
    points = [
        [
            int(datetime.now(timezone.utc).timestamp() - (len(values) - i - 1) * 60)
            * 1000,
            val,
        ]
        for i, val in enumerate(values)
    ]

    return {
        "series": [
            {
                "metric": metric_name,
                "pointlist": points,
                "unit": [{"short_name": unit}],
                "scope": f"dbinstanceidentifier:{instance_id}",
            }
        ]
    }


def create_mock_instances_response(instance_ids):
    """Helper to create mock response for instance list query."""
    series = []
    for instance_id in instance_ids:
        series.append(
            {
                "metric": "aws.rds.cpuutilization",
                "scope": f"dbinstanceidentifier:{instance_id},engine:postgres",
                "pointlist": [[datetime.now(timezone.utc).timestamp() * 1000, 50.0]],
            }
        )
    return {"series": series}


@patch(
    "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
)
def test_generate_performance_report_success(mock_execute, datadog_rds_toolset):
    """Test successful performance report generation."""
    # Mock responses for different metric groups
    mock_execute.side_effect = [
        # Latency metrics
        create_mock_metric_response("aws.rds.read_latency", [5.2, 6.1, 5.8, 7.2, 6.5]),
        create_mock_metric_response(
            "aws.rds.write_latency", [8.1, 9.2, 8.5, 12.1, 10.3]
        ),
        {"series": []},  # Commit latency - no data
        create_mock_metric_response(
            "aws.rds.disk_queue_depth", [2.1, 3.2, 2.8, 4.5, 3.9], ""
        ),
        # Resource metrics
        create_mock_metric_response(
            "aws.rds.cpuutilization", [45.2, 52.1, 48.5, 62.1, 55.3], "%"
        ),
        create_mock_metric_response(
            "aws.rds.database_connections", [120, 135, 128, 145, 140], "connections"
        ),
        create_mock_metric_response(
            "aws.rds.freeable_memory",
            [
                2048 * 1024 * 1024,
                1856 * 1024 * 1024,
                1984 * 1024 * 1024,
                1728 * 1024 * 1024,
                1920 * 1024 * 1024,
            ],
            "bytes",
        ),
        create_mock_metric_response("aws.rds.swap_usage", [0, 0, 0, 0, 0], "bytes"),
        # Storage metrics
        create_mock_metric_response(
            "aws.rds.read_iops", [1500, 2200, 1800, 2500, 2000], "iops"
        ),
        create_mock_metric_response(
            "aws.rds.write_iops", [800, 1200, 950, 1300, 1100], "iops"
        ),
        create_mock_metric_response("aws.rds.burst_balance", [85, 80, 75, 70, 65], "%"),
        create_mock_metric_response(
            "aws.rds.free_storage_space",
            [50 * 1024**3, 49 * 1024**3, 48 * 1024**3, 47 * 1024**3, 46 * 1024**3],
            "bytes",
        ),
    ]

    tool = next(
        t
        for t in datadog_rds_toolset.tools
        if t.name == "datadog_rds_performance_report"
    )
    params = {
        "db_instance_identifier": "test-instance",
        "start_time": "-3600",
    }

    result = tool._invoke(params, context=create_mock_tool_invoke_context())

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert isinstance(result.data, str)

    # Check report content
    report = result.data
    assert "RDS Performance Report - test-instance" in report
    assert "EXECUTIVE SUMMARY" in report
    assert "LATENCY METRICS" in report
    assert "RESOURCES METRICS" in report
    assert "STORAGE METRICS" in report
    assert (
        "Database is operating within normal parameters. No significant issues detected."
        in report
    )

    # Verify no issues section when everything is normal
    assert "ISSUES DETECTED" not in report


@patch(
    "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
)
def test_generate_performance_report_with_issues(mock_execute, datadog_rds_toolset):
    """Test performance report with metrics triggering issues."""
    # Mock responses with problematic values
    mock_execute.side_effect = [
        # Latency metrics - high values
        create_mock_metric_response(
            "aws.rds.read_latency", [15.2, 18.1, 55.8, 17.2, 16.5]
        ),
        create_mock_metric_response(
            "aws.rds.write_latency", [22.1, 65.2, 28.5, 32.1, 25.3]
        ),
        {"series": []},
        create_mock_metric_response(
            "aws.rds.disk_queue_depth", [8.1, 12.2, 9.8, 15.5, 10.9], ""
        ),
        # Resource metrics - high CPU, low memory
        create_mock_metric_response(
            "aws.rds.cpuutilization", [75.2, 82.1, 78.5, 92.1, 85.3], "%"
        ),
        create_mock_metric_response(
            "aws.rds.database_connections", [120, 135, 128, 145, 140], "connections"
        ),
        create_mock_metric_response(
            "aws.rds.freeable_memory",
            [
                80 * 1024 * 1024,
                60 * 1024 * 1024,
                70 * 1024 * 1024,
                50 * 1024 * 1024,
                65 * 1024 * 1024,
            ],
            "bytes",
        ),
        create_mock_metric_response(
            "aws.rds.swap_usage",
            [1024 * 1024, 2048 * 1024, 1536 * 1024, 3072 * 1024, 2560 * 1024],
            "bytes",
        ),
        # Storage metrics - low burst balance
        create_mock_metric_response(
            "aws.rds.read_iops", [3500, 4200, 3800, 4500, 4000], "iops"
        ),
        create_mock_metric_response(
            "aws.rds.write_iops", [1500, 1800, 1650, 2000, 1750], "iops"
        ),
        create_mock_metric_response("aws.rds.burst_balance", [25, 20, 15, 10, 5], "%"),
        create_mock_metric_response(
            "aws.rds.free_storage_space",
            [20 * 1024**3, 19 * 1024**3, 18 * 1024**3, 17 * 1024**3, 16 * 1024**3],
            "bytes",
        ),
    ]

    tool = next(
        t
        for t in datadog_rds_toolset.tools
        if t.name == "datadog_rds_performance_report"
    )
    result = tool._invoke(
        {"db_instance_identifier": "test-instance", "start_time": "-3600"},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS

    report = result.data
    assert "ISSUES DETECTED" in report
    assert "High severity" in report

    # Check specific issues are mentioned
    assert "latency" in report.lower()
    assert "cpu" in report.lower()
    assert "memory" in report.lower()
    assert "swap" in report.lower()
    assert "burst balance" in report.lower()
    assert "disk queue" in report.lower()


@patch(
    "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
)
def test_get_top_worst_performing_instances(mock_execute, datadog_rds_toolset):
    """Test getting top worst performing instances."""
    # Define metrics for each instance
    instance_metrics = {
        "instance-1": {
            "aws.rds.read_latency": 25.0,
            "aws.rds.write_latency": 35.0,
            "aws.rds.cpuutilization": 85.0,
            "aws.rds.database_connections": 200,
            "aws.rds.burst_balance": 20.0,
        },
        "instance-2": {
            "aws.rds.read_latency": 15.0,
            "aws.rds.write_latency": 20.0,
            "aws.rds.cpuutilization": 60.0,
            "aws.rds.database_connections": 150,
            "aws.rds.burst_balance": 50.0,
        },
        "instance-3": {
            "aws.rds.read_latency": 5.0,
            "aws.rds.write_latency": 8.0,
            "aws.rds.cpuutilization": 30.0,
            "aws.rds.database_connections": 80,
            "aws.rds.burst_balance": 90.0,
        },
    }

    def mock_response(url, **kwargs):
        # First call is instance discovery
        if not hasattr(mock_response, "call_count"):
            mock_response.call_count = 0
        mock_response.call_count += 1

        if mock_response.call_count == 1:
            return create_mock_instances_response(
                ["instance-1", "instance-2", "instance-3"]
            )

        # Extract query from payload
        query = kwargs.get("payload_or_params", {}).get("query", "")

        # Parse metric and instance from query
        for instance_id, metrics in instance_metrics.items():
            if f"dbinstanceidentifier:{instance_id}" in query:
                for metric_name, value in metrics.items():
                    if metric_name in query:
                        unit = (
                            "%"
                            if "cpu" in metric_name
                            else "connections"
                            if "connections" in metric_name
                            else ""
                        )
                        return create_mock_metric_response(
                            metric_name, [value], unit, instance_id
                        )

        return {"series": []}

    mock_execute.side_effect = mock_response

    tool = next(
        t
        for t in datadog_rds_toolset.tools
        if t.name == "datadog_rds_top_worst_performing"
    )
    params = {
        "top_n": 2,
        "start_time": "-3600",
        "sort_by": "latency",
    }

    result = tool._invoke(params, context=create_mock_tool_invoke_context())

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert isinstance(result.data, str)

    # Check report format
    report = result.data
    assert "Top Worst Performing RDS Instances" in report
    assert "Sorted by: latency" in report
    assert "Total instances analyzed: 3" in report

    # Check that instances are included in the report
    assert "instance-1" in report  # Highest latency
    assert "instance-2" in report  # Second highest

    # Check JSON data is included
    assert "Instances:" in report
    assert json.loads(report.split("Instances:\n")[1])  # Should be valid JSON


@patch(
    "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
)
def test_top_worst_performing_sort_by_cpu(mock_execute, datadog_rds_toolset):
    """Test sorting instances by CPU utilization."""
    # Define metrics for each instance
    instance_metrics = {
        "instance-1": {
            "aws.rds.read_latency": 5.0,
            "aws.rds.write_latency": 8.0,
            "aws.rds.cpuutilization": 95.0,  # High CPU
            "aws.rds.database_connections": 100,
            "aws.rds.burst_balance": 80.0,
        },
        "instance-2": {
            "aws.rds.read_latency": 25.0,
            "aws.rds.write_latency": 30.0,
            "aws.rds.cpuutilization": 40.0,  # Low CPU
            "aws.rds.database_connections": 80,
            "aws.rds.burst_balance": 70.0,
        },
    }

    def mock_response(url, **kwargs):
        if not hasattr(mock_response, "call_count"):
            mock_response.call_count = 0
        mock_response.call_count += 1

        if mock_response.call_count == 1:
            return create_mock_instances_response(["instance-1", "instance-2"])

        query = kwargs.get("payload_or_params", {}).get("query", "")

        for instance_id, metrics in instance_metrics.items():
            if f"dbinstanceidentifier:{instance_id}" in query:
                for metric_name, value in metrics.items():
                    if metric_name in query:
                        unit = (
                            "%"
                            if "cpu" in metric_name
                            else "connections"
                            if "connections" in metric_name
                            else ""
                        )
                        return create_mock_metric_response(
                            metric_name, [value], unit, instance_id
                        )

        return {"series": []}

    mock_execute.side_effect = mock_response

    tool = next(
        t
        for t in datadog_rds_toolset.tools
        if t.name == "datadog_rds_top_worst_performing"
    )
    result = tool._invoke(
        {"top_n": 10, "start_time": "-3600", "sort_by": "cpu"},
        context=create_mock_tool_invoke_context(),
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    report = result.data

    # When sorted by CPU, instance-1 (95%) should appear before instance-2 (40%)
    assert "Sorted by: cpu" in report
    # Check that instance-1 appears in the ranking
    lines = report.split("\n")
    instance_1_line = None
    instance_2_line = None
    for i, line in enumerate(lines):
        if "1. instance-1" in line:
            instance_1_line = i
        elif "2. instance-2" in line:
            instance_2_line = i

    # instance-1 should be ranked higher (appear first)
    if instance_1_line is not None and instance_2_line is not None:
        assert instance_1_line < instance_2_line


@patch(
    "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
)
def test_no_instances_found(mock_execute, datadog_rds_toolset):
    """Test handling when no instances are found."""
    # Return empty series
    mock_execute.return_value = {"series": []}

    tool = next(
        t
        for t in datadog_rds_toolset.tools
        if t.name == "datadog_rds_top_worst_performing"
    )
    result = tool._invoke(
        {"top_n": 5, "start_time": "-3600"}, context=create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.NO_DATA
    assert "No RDS instances found" in result.data


@patch(
    "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
)
def test_api_error_handling(mock_execute, datadog_rds_toolset):
    """Test handling of API errors."""
    # Simulate API error for all requests
    mock_execute.side_effect = DataDogRequestError(
        payload={"query": "test"},
        status_code=403,
        response_text="Forbidden",
        response_headers={},
    )

    tool = next(
        t
        for t in datadog_rds_toolset.tools
        if t.name == "datadog_rds_performance_report"
    )
    result = tool._invoke(
        {"db_instance_identifier": "test-instance", "start_time": "-3600"},
        context=create_mock_tool_invoke_context(),
    )

    # The tool should succeed but with no metrics collected due to API errors
    assert result.status == StructuredToolResultStatus.SUCCESS
    report = result.data
    assert (
        "Database is operating within normal parameters. No significant issues detected."
        in report
    )

    # When API errors occur, no metric sections are included in the report
    assert "LATENCY METRICS" not in report
    assert "RESOURCES METRICS" not in report
    assert "STORAGE METRICS" not in report


def test_missing_required_parameter(datadog_rds_toolset):
    """Test handling of missing required parameters."""
    tool = next(
        t
        for t in datadog_rds_toolset.tools
        if t.name == "datadog_rds_performance_report"
    )

    # Missing db_instance_identifier
    result = tool._invoke(
        {"start_time": "-3600"}, context=create_mock_tool_invoke_context()
    )

    assert result.status == StructuredToolResultStatus.ERROR
    assert "db_instance_identifier" in result.error


def test_prerequisites_check_with_valid_config():
    """Test prerequisites check with valid configuration."""
    toolset = DatadogRDSToolset()

    valid_config = {
        "dd_api_key": "test_api_key",
        "dd_app_key": "test_app_key",
        "site_api_url": "https://api.datadoghq.com",
    }

    with patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
    ) as mock_execute:
        # Mock successful validation
        mock_execute.return_value = {"valid": True}

        prereq = toolset.prerequisites_check(valid_config)
        success, message = prereq.callable(valid_config)

        assert success is True
        assert message == ""


def test_prerequisites_check_with_invalid_credentials():
    """Test prerequisites check with invalid credentials."""
    toolset = DatadogRDSToolset()

    config = {
        "dd_api_key": "invalid_key",
        "dd_app_key": "invalid_app_key",
        "site_api_url": "https://api.datadoghq.com",
    }

    with patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
    ) as mock_execute:
        # Mock 403 error
        mock_execute.side_effect = DataDogRequestError(
            payload={}, status_code=403, response_text="Forbidden", response_headers={}
        )

        prereq = toolset.prerequisites_check(config)
        success, message = prereq.callable(config)

        assert success is False
        assert "Invalid Datadog API keys" in message


def test_get_example_config():
    """Test example configuration generation."""
    toolset = DatadogRDSToolset()
    example_config = toolset.get_example_config()

    assert "dd_api_key" in example_config
    assert "dd_app_key" in example_config
    assert "site_api_url" in example_config
    assert "default_time_span_seconds" in example_config
    assert "default_top_instances" in example_config


def test_performance_report_formatting(datadog_rds_toolset):
    """Test that performance report is properly formatted."""
    with patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
    ) as mock_execute:
        # Mock minimal metrics
        mock_execute.side_effect = [
            create_mock_metric_response("aws.rds.read_latency", [5.0]),
            {"series": []},  # write_latency - no data
            {"series": []},  # commit_latency - no data
            {"series": []},  # disk_queue_depth - no data
            create_mock_metric_response("aws.rds.cpuutilization", [50.0], "%"),
            {"series": []},  # database_connections - no data
            {"series": []},  # freeable_memory - no data
            {"series": []},  # swap_usage - no data
            {"series": []},  # read_iops - no data
            {"series": []},  # write_iops - no data
            {"series": []},  # burst_balance - no data
            {"series": []},  # free_storage_space - no data
        ]

        tool = next(
            t
            for t in datadog_rds_toolset.tools
            if t.name == "datadog_rds_performance_report"
        )
        result = tool._invoke(
            {"db_instance_identifier": "test-db", "start_time": "-300"},
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        report = result.data

        # Check report structure
        assert "RDS Performance Report - test-db" in report
        assert "Generated:" in report
        assert "Time Range:" in report
        assert "EXECUTIVE SUMMARY" in report

        # Check metrics sections exist even with partial data
        assert "LATENCY METRICS" in report
        assert "Read Latency" in report
        assert "5.00" in report  # The metric value

        assert "RESOURCES METRICS" in report
        assert "CPU Utilization" in report
        assert "50.00" in report  # The metric value


def test_top_worst_performing_no_metrics(datadog_rds_toolset):
    """Test worst performing when instances have no metrics."""
    with patch(
        "holmes.plugins.toolsets.datadog.toolset_datadog_rds.execute_datadog_http_request"
    ) as mock_execute:

        def mock_response(url, **kwargs):
            if not hasattr(mock_response, "call_count"):
                mock_response.call_count = 0
            mock_response.call_count += 1

            if mock_response.call_count == 1:
                # Return instances
                return create_mock_instances_response(["instance-1", "instance-2"])

            # Return no metrics for any query
            return {"series": []}

        mock_execute.side_effect = mock_response

        tool = next(
            t
            for t in datadog_rds_toolset.tools
            if t.name == "datadog_rds_top_worst_performing"
        )
        result = tool._invoke(
            {"top_n": 5, "start_time": "-3600"},
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        report = result.data

        # Should show 0 instances analyzed (no metrics found)
        assert "Total instances analyzed: 0" in report
        assert "Instances shown: 0" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
