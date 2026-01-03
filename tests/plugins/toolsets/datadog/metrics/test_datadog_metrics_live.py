import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from holmes.plugins.toolsets.datadog.toolset_datadog_metrics import (
    DatadogMetricsToolset,
)
from tests.conftest import create_mock_tool_invoke_context


@pytest.mark.skipif(
    not all([os.getenv("DD_API_KEY"), os.getenv("DD_APP_KEY")]),
    reason="Datadog API credentials not available",
)
class TestDatadogMetricsLiveIntegration:
    """
    Live integration tests for Datadog metrics toolset.
    These tests require valid Datadog API credentials set as environment variables.
    """

    def setup_method(self):
        """Setup the toolset with real Datadog credentials."""
        self.config = {
            "dd_api_key": os.getenv("DD_API_KEY"),
            "dd_app_key": os.getenv("DD_APP_KEY"),
            "site_api_url": os.getenv("DD_SITE_URL", "https://api.datadoghq.eu"),
            "default_limit": 1000,
            "request_timeout": 60,
        }

        self.toolset = DatadogMetricsToolset()
        success, error_msg = self.toolset.prerequisites_callable(self.config)
        assert success, f"Failed to initialize toolset: {error_msg}"

    def test_list_active_metrics_live(self):
        """Test listing active metrics from the live Datadog instance."""
        list_metrics_tool = self.toolset.tools[0]
        assert list_metrics_tool.name == "list_active_datadog_metrics"

        # List metrics from the last hour
        params = {"from_time": "-3600"}  # 1 hour ago

        result = list_metrics_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        assert (
            result.status.value == "success"
        ), f"Failed to list metrics: {result.error}"
        assert "Metric Name" in result.data
        assert "-" * 50 in result.data

        # Verify we got some metrics
        lines = result.data.split("\n")
        metric_lines = [
            line
            for line in lines
            if line and not line.startswith("-") and line != "Metric Name"
        ]
        assert len(metric_lines) > 0, "No metrics found"

        print(f"Found {len(metric_lines)} active metrics")
        print(f"Sample metrics: {metric_lines[:5]}")

    def test_list_metrics_with_kubernetes_filter(self):
        """Test listing metrics filtered by Kubernetes-related tags."""
        list_metrics_tool = self.toolset.tools[0]

        # Look for Kubernetes node metrics
        params = {
            "from_time": "-3600",  # 1 hour ago
            "tag_filter": "kube_node_name:kind-double-node-control-plane",
        }

        result = list_metrics_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        if result.status.value == "success":
            lines = result.data.split("\n")
            metric_lines = [
                line
                for line in lines
                if line and not line.startswith("-") and line != "Metric Name"
            ]
            print(f"Found {len(metric_lines)} metrics for control-plane node")

            # Look for common Kubernetes metrics
            k8s_metrics = [
                m
                for m in metric_lines
                if any(kw in m for kw in ["kubernetes", "kube", "container", "pod"])
            ]
            if k8s_metrics:
                print(f"Found {len(k8s_metrics)} Kubernetes-related metrics")
                print(f"Sample K8s metrics: {k8s_metrics[:5]}")

    def test_query_kubernetes_cpu_metrics(self):
        """Test querying CPU metrics for Kubernetes nodes."""
        query_metrics_tool = self.toolset.tools[1]
        assert query_metrics_tool.name == "query_datadog_metrics"

        # Query CPU metrics for the control plane node
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=30)

        queries_to_test = [
            "avg:system.cpu.user{*}",
            "avg:kubernetes.cpu.usage.total{*}",
            "avg:container.cpu.usage{*}",
        ]

        successful_queries = []

        for query in queries_to_test:
            params = {
                "query": query,
                "from_time": start_time.isoformat(),
                "to_time": end_time.isoformat(),
            }

            result = query_metrics_tool._invoke(
                params, context=create_mock_tool_invoke_context()
            )

            if result.status.value == "success":
                data = json.loads(result.data)
                # Check for data.data.result which is the Prometheus response format
                if (
                    "data" in data
                    and isinstance(data["data"], dict)
                    and "result" in data["data"]
                ):
                    series = data["data"]["result"]
                else:
                    series = data.get("series")
                if series and len(series) > 0:
                    successful_queries.append(
                        {
                            "query": query,
                            "series_count": len(series),
                            "first_series": series[0],
                        }
                    )
                    print(f"Successfully queried: {query}")
                    print(f"  Found {len(series)} series")

                    # Validate the series structure
                    for s in series:
                        assert "metric" in s
                        # Datadog returns either "points", "pointlist", or "values" (Prometheus format)
                        if "values" in s:
                            points_key = "values"
                        else:
                            points_key = "pointlist" if "pointlist" in s else "points"
                        assert points_key in s
                        assert isinstance(s[points_key], list)
                        if s[points_key]:
                            # Each point should be [timestamp, value]
                            assert len(s[points_key][0]) == 2
                            assert isinstance(s[points_key][0][0], (int, float))
                            assert isinstance(s[points_key][0][1], (int, float, str))

        # Ensure we found at least one metric
        assert (
            len(successful_queries) > 0
        ), "No metrics found - check if Datadog has data for these metrics"
        print(
            f"\nSuccessfully queried {len(successful_queries)} out of {len(queries_to_test)} metrics"
        )

    def test_query_specific_pod_metrics(self):
        """Test querying metrics for specific pods in the cluster."""
        query_metrics_tool = self.toolset.tools[1]

        # Query metrics for Datadog agent pod
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=15)

        # Try to find metrics for the datadog-agent pods
        params = {
            "query": "avg:kubernetes.memory.usage{pod_name:datadog-agent-*} by {pod_name}",
            "from_time": start_time.isoformat(),
            "to_time": end_time.isoformat(),
        }

        result = query_metrics_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        if result.status.value == "success":
            data = json.loads(result.data)
            print(f"Datadog agent memory query result: {data.get('status')}")
            if data.get("series"):
                print(f"Found {len(data['series'])} series for Datadog agent pods")
                for series in data["series"][:3]:  # Show first 3
                    tags = series.get("tags", [])
                    pod_name = next(
                        (
                            tag.split(":")[1]
                            for tag in tags
                            if tag.startswith("pod_name:")
                        ),
                        "unknown",
                    )
                    points_count = len(series.get("points", []))
                    print(f"  Pod: {pod_name}, Data points: {points_count}")

    def test_get_metric_metadata(self):
        """Test getting metadata for common metrics."""
        metadata_tool = self.toolset.tools[2]
        assert metadata_tool.name == "get_datadog_metric_metadata"

        # Test single metric
        params_single = {"metric_names": "system.cpu.user"}
        result = metadata_tool._invoke(
            params_single, context=create_mock_tool_invoke_context()
        )

        assert result.status.value == "success"
        data = json.loads(result.data)
        assert "metrics_metadata" in data
        assert data["successful"] >= 1 or data["failed"] >= 0

        # Test multiple metrics at once
        metrics_to_check = [
            "system.cpu.user",
            "system.mem.used",
            "kubernetes.cpu.usage.total",
            "nonexistent.metric.test",  # Include one that might not exist
        ]

        params = {"metric_names": ", ".join(metrics_to_check)}
        result = metadata_tool._invoke(
            params, context=create_mock_tool_invoke_context()
        )

        assert result.status.value == "success"
        data = json.loads(result.data)

        print("\nMetadata query results:")
        print(f"  Total requested: {data['total_requested']}")
        print(f"  Successful: {data['successful']}")
        print(f"  Failed: {data['failed']}")

        # Check successful metadata
        for metric_name, metadata in data.get("metrics_metadata", {}).items():
            print(f"\nMetadata for {metric_name}:")
            print(f"  Type: {metadata.get('type', 'N/A')}")
            print(f"  Unit: {metadata.get('unit', 'N/A')}")
            print(f"  Description: {metadata.get('description', 'N/A')[:100]}...")

            # Common metadata fields validation
            if "type" in metadata:
                assert metadata["type"] in ["gauge", "count", "rate", "distribution"]

        # Check errors
        for metric_name, error in data.get("errors", {}).items():
            print(f"\nError for {metric_name}: {error}")

        assert data["successful"] > 0, "No metadata retrieved for any metric"

    def test_error_handling_invalid_metric(self):
        """Test error handling for invalid metric queries."""
        query_tool = self.toolset.tools[1]

        # Use recent timestamps to avoid issues
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=1)

        params = {
            "query": "this.is.not.a.valid.metric{*}",
            "from_time": start_time.isoformat(),
            "to_time": end_time.isoformat(),
        }

        result = query_tool._invoke(params, context=create_mock_tool_invoke_context())

        # Should either return NO_DATA or SUCCESS with empty series
        assert result.status.value in ["no_data", "success"]
        if result.status.value == "success":
            data = json.loads(result.data)
            assert data.get("series") == []

    def test_validate_response_structure(self):
        """Test that all responses follow the expected structure."""
        list_tool = self.toolset.tools[0]
        query_tool = self.toolset.tools[1]
        metadata_tool = self.toolset.tools[2]

        # Test list metrics response
        list_result = list_tool._invoke(
            {"from_time": int(time.time() - 3600)},
            context=create_mock_tool_invoke_context(),
        )
        assert hasattr(list_result, "status")
        assert hasattr(list_result, "data") or hasattr(list_result, "error")
        assert hasattr(list_result, "params")

        # Test query metrics response
        query_result = query_tool._invoke(
            {
                "query": "avg:system.load.1{*}",
                "from_time": "2024-01-01T00:00:00Z",
                "to_time": "2024-01-01T01:00:00Z",
            },
            context=create_mock_tool_invoke_context(),
        )
        assert hasattr(query_result, "status")
        assert hasattr(query_result, "data") or hasattr(query_result, "error")

        # Test metadata response
        metadata_result = metadata_tool._invoke(
            {"metric_name": "system.cpu.idle"},
            context=create_mock_tool_invoke_context(),
        )
        assert hasattr(metadata_result, "status")
        assert hasattr(metadata_result, "data") or hasattr(metadata_result, "error")

    def test_time_range_handling(self):
        """Test different time range formats and edge cases."""
        query_tool = self.toolset.tools[1]

        # Test with ISO format timestamps
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=1)

        # Use a more common metric that's likely to have data
        params = {
            "query": "avg:system.cpu.user{*}",
            "from_time": start_time.isoformat(),
            "to_time": end_time.isoformat(),
        }

        result = query_tool._invoke(params, context=create_mock_tool_invoke_context())
        assert result.status.value in ["success", "no_data"]

        # Test with relative time (negative integer)
        params_relative = {
            "query": "avg:system.cpu.user{*}",
            "from_time": "-3600",  # 1 hour ago
            "to_time": end_time.isoformat(),
        }

        result = query_tool._invoke(
            params_relative, context=create_mock_tool_invoke_context()
        )
        assert result.status.value in ["success", "no_data"]

        # Test with missing time parameters (should use defaults)
        params_no_time = {"query": "avg:system.cpu.user{*}"}
        result_no_time = query_tool._invoke(
            params_no_time, context=create_mock_tool_invoke_context()
        )
        assert result_no_time.status.value in ["success", "no_data"]

        if result_no_time.status.value == "success":
            data = json.loads(result_no_time.data)
            # Should have time information in the response
            # The actual field names may vary depending on the metric type
            assert data is not None
            assert "data" in data or "series" in data or "result" in data
