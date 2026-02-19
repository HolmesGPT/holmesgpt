from unittest.mock import MagicMock, patch

from holmes.core.tools import (
    StructuredToolResultStatus,
)
from holmes.plugins.toolsets.grafana.common import GrafanaTempoConfig
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
    FetchTracesSimpleComparison,
    GrafanaTempoToolset,
)
from tests.conftest import create_mock_tool_invoke_context


def test_fetch_traces_simple_comparison_has_prompt():
    """Test that FetchTracesSimpleComparison tool has proper metadata."""
    toolset = GrafanaTempoToolset()
    tool = FetchTracesSimpleComparison(toolset)
    assert tool.name == "tempo_fetch_traces_comparative_sample"
    assert tool.name is not None
    assert toolset.llm_instructions is not None
    assert tool.name in toolset.llm_instructions


def test_all_tempo_tools_have_prompts():
    """Test that all Tempo tools have proper metadata."""
    toolset = GrafanaTempoToolset()
    # Check FetchTracesSimpleComparison specifically
    tool = FetchTracesSimpleComparison(toolset)
    assert tool.name is not None
    assert tool.name in toolset.llm_instructions


def test_fetch_traces_simple_comparison_validation():
    """Test parameter validation for FetchTracesSimpleComparison."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config
    tool = FetchTracesSimpleComparison(toolset)

    # Test with no parameters - should fail validation
    context = create_mock_tool_invoke_context()
    result = tool.invoke(params={}, context=context)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "At least one of the following argument is expected" in result.error


def test_fetch_traces_simple_comparison_with_mocked_data():
    """Test FetchTracesSimpleComparison with mocked Tempo responses."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config
    tool = FetchTracesSimpleComparison(toolset)

    # Mock trace data
    mock_traces = {
        "traces": [
            {
                "traceID": "fast-trace-1",
                "rootServiceName": "frontend",
                "durationMs": 50,
                "startTimeUnixNano": "1609459200000000000",
            },
            {
                "traceID": "fast-trace-2",
                "rootServiceName": "frontend",
                "durationMs": 75,
                "startTimeUnixNano": "1609459300000000000",
            },
            {
                "traceID": "medium-trace",
                "rootServiceName": "frontend",
                "durationMs": 150,
                "startTimeUnixNano": "1609459400000000000",
            },
            {
                "traceID": "slow-trace-1",
                "rootServiceName": "frontend",
                "durationMs": 500,
                "startTimeUnixNano": "1609459500000000000",
            },
            {
                "traceID": "slow-trace-2",
                "rootServiceName": "frontend",
                "durationMs": 750,
                "startTimeUnixNano": "1609459600000000000",
            },
        ]
    }

    # Mock full trace data
    mock_full_trace = {
        "batches": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "frontend"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "test-trace",
                                "spanId": "test-span",
                                "name": "GET /api/data",
                                "startTimeUnixNano": "1609459200000000000",
                                "endTimeUnixNano": "1609459200050000000",
                            }
                        ]
                    }
                ],
            }
        ]
    }

    with patch(
        "holmes.plugins.toolsets.grafana.toolset_grafana_tempo.GrafanaTempoAPI"
    ) as mock_api_class:
        # Create mock API instance
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api

        # Mock search_traces_by_query
        mock_api.search_traces_by_query.return_value = mock_traces

        # Mock query_trace_by_id_v2 for individual trace fetches
        mock_api.query_trace_by_id_v2.return_value = mock_full_trace

        # Test with service name filter
        context = create_mock_tool_invoke_context()
        result = tool.invoke(
            params={
                "service_name": "frontend",
                "sample_count": 2,
                "start": "-3600",
                "end": "0",
            },
            context=context,
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None

        # Data is now a dict, not YAML string
        data = result.data

        # Verify statistics
        assert "statistics" in data
        stats = data["statistics"]
        assert stats["trace_count"] == 5
        assert stats["min_ms"] == 50
        assert stats["max_ms"] == 750
        assert stats["p50_ms"] == 150

        # Verify we got the correct number of samples
        assert len(data["fastest_traces"]) == 2
        assert len(data["slowest_traces"]) == 2
        assert data["median_trace"] is not None

        # Verify trace ordering
        assert data["fastest_traces"][0]["durationMs"] == 50
        assert data["fastest_traces"][1]["durationMs"] == 75
        assert data["median_trace"]["durationMs"] == 150
        assert data["slowest_traces"][0]["durationMs"] == 500
        assert data["slowest_traces"][1]["durationMs"] == 750

        # Verify the query was called correctly
        mock_api.search_traces_by_query.assert_called_once()
        call_args = mock_api.search_traces_by_query.call_args[1]
        assert 'resource.service.name=~".*frontend.*"' in call_args["q"]


def test_fetch_traces_simple_comparison_with_multiple_filters():
    """Test FetchTracesSimpleComparison with multiple K8s filters."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config
    tool = FetchTracesSimpleComparison(toolset)

    mock_traces = {"traces": []}

    with patch(
        "holmes.plugins.toolsets.grafana.toolset_grafana_tempo.GrafanaTempoAPI"
    ) as mock_api_class:
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.search_traces_by_query.return_value = mock_traces

        result = tool.invoke(
            params={
                "service_name": "api",
                "namespace_name": "production",
                "deployment_name": "api-server",
                "pod_name": "api-pod",
                "node_name": "node-1",
                "sample_count": 5,
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "No traces found matching the query"

        # Verify all filters were included in the query
        call_args = mock_api.search_traces_by_query.call_args[1]
        query = call_args["q"]
        assert 'resource.service.name=~".*api.*"' in query
        assert 'resource.k8s.namespace.name=~".*production.*"' in query
        assert 'resource.k8s.deployment.name=~".*api-server.*"' in query
        assert 'resource.k8s.pod.name=~".*api-pod.*"' in query
        assert 'resource.k8s.node.name=~".*node-1.*"' in query


def test_fetch_traces_simple_comparison_with_base_query():
    """Test FetchTracesSimpleComparison with a custom base query."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config
    tool = FetchTracesSimpleComparison(toolset)

    mock_traces = {
        "traces": [
            {
                "traceID": "trace-1",
                "rootServiceName": "custom-service",
                "durationMs": 100,
                "startTimeUnixNano": "1609459200000000000",
            }
        ]
    }

    with patch(
        "holmes.plugins.toolsets.grafana.toolset_grafana_tempo.GrafanaTempoAPI"
    ) as mock_api_class:
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.search_traces_by_query.return_value = mock_traces
        mock_api.query_trace_by_id_v2.return_value = {"batches": []}

        custom_query = "span.http.status_code >= 400"
        context = create_mock_tool_invoke_context()
        result = tool.invoke(params={"base_query": custom_query}, context=context)

        assert result.status == StructuredToolResultStatus.SUCCESS

        # Verify the custom query was used
        call_args = mock_api.search_traces_by_query.call_args[1]
        assert call_args["q"] == f"{{{custom_query}}}"


def test_fetch_traces_simple_comparison_error_handling():
    """Test error handling in FetchTracesSimpleComparison."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config
    tool = FetchTracesSimpleComparison(toolset)

    with patch(
        "holmes.plugins.toolsets.grafana.toolset_grafana_tempo.GrafanaTempoAPI"
    ) as mock_api_class:
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.search_traces_by_query.side_effect = Exception("API Error")

        context = create_mock_tool_invoke_context()
        result = tool.invoke(params={"service_name": "test-service"}, context=context)

        assert result.status == StructuredToolResultStatus.ERROR
        assert "Error fetching traces: API Error" in result.error


def test_fetch_traces_simple_comparison_percentile_calculations():
    """Test percentile calculations with various trace counts."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config
    tool = FetchTracesSimpleComparison(toolset)

    # Test with exactly 3 traces (edge case for percentiles)
    mock_traces = {
        "traces": [
            {"traceID": f"trace-{i}", "durationMs": i * 100}
            for i in range(1, 4)  # 100, 200, 300
        ]
    }

    with patch(
        "holmes.plugins.toolsets.grafana.toolset_grafana_tempo.GrafanaTempoAPI"
    ) as mock_api_class:
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.search_traces_by_query.return_value = mock_traces
        mock_api.query_trace_by_id_v2.return_value = {"batches": []}

        context = create_mock_tool_invoke_context()
        result = tool.invoke(params={"service_name": "test"}, context=context)
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None

        data = result.data  # Data is now a dict, not YAML string

        stats = data["statistics"]
        assert stats["trace_count"] == 3
        assert stats["min_ms"] == 100
        assert stats["p50_ms"] == 200
        assert stats["max_ms"] == 300
        # With only 3 traces, p25 should be 100 (first element)
        assert stats["p25_ms"] == 100


def test_fetch_traces_simple_comparison_parameterized_one_liner():
    """Test the parameterized one liner for FetchTracesSimpleComparison."""
    toolset = GrafanaTempoToolset()
    tool = FetchTracesSimpleComparison(toolset)

    params = {"service_name": "test-service", "sample_count": 5}
    one_liner = tool.get_parameterized_one_liner(params)
    assert "Simple Tempo Traces Comparison" in one_liner
    assert "Grafana" in one_liner


def test_build_k8s_filters():
    """Test the shared build_k8s_filters utility method."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config

    # Test exact match filters
    params = {
        "service_name": "my-service",
        "pod_name": "my-pod",
        "namespace_name": "my-namespace",
        "deployment_name": "my-deployment",
        "node_name": "my-node",
    }

    exact_filters = toolset.build_k8s_filters(params, use_exact_match=True)
    assert len(exact_filters) == 5
    assert 'resource.service.name="my-service"' in exact_filters
    assert 'resource.k8s.pod.name="my-pod"' in exact_filters
    assert 'resource.k8s.namespace.name="my-namespace"' in exact_filters
    assert 'resource.k8s.deployment.name="my-deployment"' in exact_filters
    assert 'resource.k8s.node.name="my-node"' in exact_filters

    # Test regex match filters
    regex_filters = toolset.build_k8s_filters(params, use_exact_match=False)
    assert len(regex_filters) == 5
    assert 'resource.service.name=~".*my-service.*"' in regex_filters
    assert 'resource.k8s.pod.name=~".*my-pod.*"' in regex_filters
    assert 'resource.k8s.namespace.name=~".*my-namespace.*"' in regex_filters
    assert 'resource.k8s.deployment.name=~".*my-deployment.*"' in regex_filters
    assert 'resource.k8s.node.name=~".*my-node.*"' in regex_filters


def test_fetch_traces_simple_comparison_with_negative_start_time():
    """Test FetchTracesSimpleComparison with negative start time."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config
    tool = FetchTracesSimpleComparison(toolset)

    mock_traces = {"traces": [{"traceID": "trace-1", "durationMs": 100}]}

    with patch(
        "holmes.plugins.toolsets.grafana.toolset_grafana_tempo.GrafanaTempoAPI"
    ) as mock_api_class:
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        mock_api.search_traces_by_query.return_value = mock_traces
        mock_api.query_trace_by_id_v2.return_value = {"batches": []}

        # Test with negative start (-7200 = 2 hours before end)
        result = tool.invoke(
            params={
                "service_name": "test",
                "start": "-7200",  # 2 hours ago
                "end": "0",  # Now
            },
            context=create_mock_tool_invoke_context(),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS

        # Verify the search was called with positive timestamps
        call_args = mock_api.search_traces_by_query.call_args[1]
        assert call_args["start"] > 0
        assert call_args["end"] > 0
        assert call_args["end"] - call_args["start"] == 7200


def test_build_k8s_filters_with_special_characters():
    """Test that special regex characters are properly escaped."""
    config = GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = config

    # Test with special regex characters
    params = {
        "service_name": "test.service[1]",
        "pod_name": "pod-with(parens)",
        "namespace_name": "namespace.*",
        "deployment_name": "deploy+test",
        "node_name": "node^name$",
    }

    # Test regex match filters - all special chars should be escaped
    regex_filters = toolset.build_k8s_filters(params, use_exact_match=False)
    assert len(regex_filters) == 5
    assert 'resource.service.name=~".*test.service[1].*"' in regex_filters
    assert 'resource.k8s.pod.name=~".*pod-with(parens).*"' in regex_filters
    assert 'resource.k8s.namespace.name=~".*namespace.*.*"' in regex_filters
    assert 'resource.k8s.deployment.name=~".*deploy+test.*"' in regex_filters
    assert 'resource.k8s.node.name=~".*node^name$.*"' in regex_filters

    # Test exact match with quotes
    params_with_quotes = {
        "service_name": 'service"with"quotes',
    }
    exact_filters = toolset.build_k8s_filters(params_with_quotes, use_exact_match=True)
    assert 'resource.service.name="service\\"with\\"quotes"' in exact_filters
