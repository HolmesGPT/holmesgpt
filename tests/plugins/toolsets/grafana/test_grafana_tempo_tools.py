from unittest.mock import ANY, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.grafana.common import GrafanaTempoConfig
from holmes.plugins.toolsets.grafana.grafana_tempo_api import TempoAPIError
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
    GrafanaTempoToolset,
    QueryMetricsInstant,
    QueryMetricsRange,
    QueryTraceById,
    SearchTagNames,
    SearchTagValues,
    SearchTracesByQuery,
    SearchTracesByTags,
)
from tests.conftest import create_mock_tool_invoke_context


@pytest.fixture
def tempo_config():
    """Create a test Tempo configuration."""
    return GrafanaTempoConfig(
        api_key="test_key",
        url="http://localhost:3000",
        grafana_datasource_uid="tempo_uid",
    )


@pytest.fixture
def tempo_toolset(tempo_config):
    """Create a GrafanaTempoToolset instance with test config."""
    toolset = GrafanaTempoToolset()
    toolset._grafana_config = tempo_config
    return toolset


class TestSearchTracesByQuery:
    def test_search_traces_by_query_success(self, tempo_toolset):
        """Test successful trace search using TraceQL."""
        tool = SearchTracesByQuery(tempo_toolset)

        mock_response = {
            "traces": [
                {"traceID": "123", "durationMs": 100},
                {"traceID": "456", "durationMs": 200},
            ]
        }

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_traces_by_query"
        ) as mock_search:
            mock_search.return_value = mock_response

            result = tool._invoke(
                {
                    "q": '{resource.service.name="api"}',
                    "limit": 10,
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert "traces" in result.data
            mock_search.assert_called_once_with(
                q='{resource.service.name="api"}',
                limit=10,
                start=ANY,
                end=ANY,
                spss=None,
            )

    def test_search_traces_by_query_error(self, tempo_toolset):
        """Test error handling in trace search."""
        tool = SearchTracesByQuery(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_traces_by_query"
        ) as mock_search:
            mock_search.side_effect = Exception("API Error")

            result = tool._invoke(
                {"q": "{invalid}"}, context=create_mock_tool_invoke_context()
            )

            assert result.status == StructuredToolResultStatus.ERROR
            assert "API Error" in result.error

    def test_search_traces_by_query_tempo_api_error(self, tempo_toolset):
        """Test that Tempo API errors are properly propagated with details."""
        tool = SearchTracesByQuery(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_traces_by_query"
        ) as mock_search:
            # Simulate a Tempo API error with detailed message
            mock_search.side_effect = TempoAPIError(
                status_code=400,
                response_text='{"error": "invalid TraceQL query: unexpected token"}',
                url="http://tempo/api/search",
            )

            result = tool._invoke(
                {"q": "{invalid syntax}"}, context=create_mock_tool_invoke_context()
            )

            assert result.status == StructuredToolResultStatus.ERROR
            assert "invalid TraceQL query: unexpected token" in result.error
            assert "400" in result.error


class TestSearchTracesByTags:
    def test_search_traces_by_tags_success(self, tempo_toolset):
        """Test successful trace search using tags."""
        tool = SearchTracesByTags(tempo_toolset)

        mock_response = {"traces": [{"traceID": "789"}]}

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_traces_by_tags"
        ) as mock_search:
            mock_search.return_value = mock_response

            result = tool._invoke(
                {
                    "tags": 'service.name="api" http.status_code="500"',
                    "min_duration": "100ms",
                    "max_duration": "5s",
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            mock_search.assert_called_once_with(
                tags='service.name="api" http.status_code="500"',
                min_duration="100ms",
                max_duration="5s",
                limit=None,
                start=ANY,
                end=ANY,
                spss=None,
            )


class TestQueryTraceById:
    def test_query_trace_by_id_success(self, tempo_toolset):
        """Test successful trace retrieval by ID."""
        tool = QueryTraceById(tempo_toolset)

        mock_trace_data = {
            "batches": [{"resource": {"attributes": []}, "scopeSpans": []}]
        }

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_trace_by_id_v2"
        ) as mock_query:
            mock_query.return_value = mock_trace_data

            result = tool._invoke(
                {"trace_id": "abc123"}, context=create_mock_tool_invoke_context()
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            mock_query.assert_called_once_with(
                trace_id="abc123",
                start=ANY,
                end=ANY,
            )

    def test_query_trace_by_id_with_time_range(self, tempo_toolset):
        """Test trace retrieval with time range."""
        tool = QueryTraceById(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_trace_by_id_v2"
        ) as mock_query:
            mock_query.return_value = {"batches": []}

            result = tool._invoke(
                {
                    "trace_id": "abc123",
                    "start": 1234567890,
                    "end": 1234567900,
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            mock_query.assert_called_once_with(
                trace_id="abc123",
                start=1234567890,
                end=1234567900,
            )


class TestSearchTagNames:
    def test_search_tag_names_success(self, tempo_toolset):
        """Test successful tag name discovery."""
        tool = SearchTagNames(tempo_toolset)

        mock_response = {
            "scopes": {
                "resource": ["service.name", "cluster"],
                "span": ["http.status_code", "http.method"],
                "intrinsic": ["duration", "name"],
            }
        }

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_tag_names_v2"
        ) as mock_search:
            mock_search.return_value = mock_response

            result = tool._invoke({}, context=create_mock_tool_invoke_context())

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert "scopes" in result.data

    def test_search_tag_names_with_filters(self, tempo_toolset):
        """Test tag name discovery with filters."""
        tool = SearchTagNames(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_tag_names_v2"
        ) as mock_search:
            mock_search.return_value = {"scopes": {}}

            result = tool._invoke(
                {
                    "scope": "resource",
                    "q": '{resource.cluster="prod"}',
                    "limit": 50,
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            mock_search.assert_called_once_with(
                scope="resource",
                q='{resource.cluster="prod"}',
                start=ANY,
                end=ANY,
                limit=50,
                max_stale_values=None,
            )


class TestSearchTagValues:
    def test_search_tag_values_success(self, tempo_toolset):
        """Test successful tag value discovery."""
        tool = SearchTagValues(tempo_toolset)

        mock_response = {
            "tagValues": [
                {"type": "string", "value": "api-service"},
                {"type": "string", "value": "web-service"},
            ]
        }

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_tag_values_v2"
        ) as mock_search:
            mock_search.return_value = mock_response

            result = tool._invoke(
                {"tag": "service.name"}, context=create_mock_tool_invoke_context()
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert "tagValues" in result.data

    def test_search_tag_values_error(self, tempo_toolset):
        """Test error handling in tag value search."""
        tool = SearchTagValues(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_tag_values_v2"
        ) as mock_search:
            mock_search.side_effect = Exception("Tag not found")

            result = tool._invoke(
                {"tag": "invalid.tag"}, context=create_mock_tool_invoke_context()
            )

            assert result.status == StructuredToolResultStatus.ERROR
            assert "Tag not found" in result.error


class TestQueryMetricsInstant:
    def test_query_metrics_instant_success(self, tempo_toolset):
        """Test successful instant metric query."""
        tool = QueryMetricsInstant(tempo_toolset)

        mock_response = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1234567890, "0.95"]}],
            },
        }

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_instant"
        ) as mock_query:
            mock_query.return_value = mock_response

            result = tool._invoke(
                {
                    "q": "{ } | histogram_quantile(.95)",
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            mock_query.assert_called_once_with(
                q="{ } | histogram_quantile(.95)",
                start=ANY,
                end=ANY,
            )


class TestQueryMetricsRange:
    def test_query_metrics_range_success(self, tempo_toolset):
        """Test successful range metric query."""
        tool = QueryMetricsRange(tempo_toolset)

        mock_response = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"service.name": "api"},
                        "values": [[1234567890, "10"], [1234567900, "15"]],
                    }
                ],
            },
        }

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_range"
        ) as mock_query:
            mock_query.return_value = mock_response

            result = tool._invoke(
                {
                    "q": '{ service.name="api" } | rate()',
                    "step": "8m",
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            assert "result" in result.data["data"]
            mock_query.assert_called_once_with(
                q='{ service.name="api" } | rate()',
                step="8m",
                start=ANY,
                end=ANY,
                exemplars=None,
            )

    def test_query_metrics_range_with_exemplars(self, tempo_toolset):
        """Test range metric query with exemplars."""
        tool = QueryMetricsRange(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_range"
        ) as mock_query:
            mock_query.return_value = {"status": "success", "data": {}}

            result = tool._invoke(
                {
                    "q": "{ } | rate()",
                    "exemplars": 100,
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            mock_query.assert_called_once_with(
                q="{ } | rate()",
                step=ANY,  # step will be auto-calculated
                start=ANY,
                end=ANY,
                exemplars=100,
            )


class TestNegativeTimestamps:
    """Test handling of negative timestamps across all tools."""

    def test_search_traces_with_negative_start(self, tempo_toolset):
        """Test search with negative start time (relative to end)."""
        tool = SearchTracesByQuery(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_traces_by_query"
        ) as mock_search:
            mock_search.return_value = {"traces": []}

            # Use negative start (-3600 = 1 hour before end)
            result = tool._invoke(
                {"q": '{resource.service.name="api"}', "start": "-3600"},
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            # Verify start was converted to a positive timestamp
            args, kwargs = mock_search.call_args
            assert kwargs["start"] > 0
            assert kwargs["end"] > kwargs["start"]
            # Start should be exactly 3600 seconds before end
            assert kwargs["end"] - kwargs["start"] == 3600

    def test_query_trace_with_negative_timestamps(self, tempo_toolset):
        """Test trace query with both negative start and end."""
        tool = QueryTraceById(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_trace_by_id_v2"
        ) as mock_query:
            mock_query.return_value = {"batches": []}

            # Both negative: end relative to now, start relative to end
            result = tool._invoke(
                {
                    "trace_id": "test123",
                    "start": "-7200",  # 2 hours before end
                    "end": "-3600",  # 1 hour before now
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            args, kwargs = mock_query.call_args
            # Both should be positive timestamps
            assert kwargs["start"] > 0
            assert kwargs["end"] > 0
            # Start should be before end
            assert kwargs["start"] < kwargs["end"]
            # Time span should be 7200 seconds (2 hours)
            assert kwargs["end"] - kwargs["start"] == 7200

    def test_metrics_with_inverted_timestamps(self, tempo_toolset):
        """Test that start/end are automatically inverted if start > end."""
        tool = QueryMetricsInstant(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_instant"
        ) as mock_query:
            mock_query.return_value = {"status": "success", "data": {}}

            # Provide timestamps in wrong order
            now = 1234567890
            result = tool._invoke(
                {
                    "q": "{ } | rate()",
                    "start": now + 3600,  # 1 hour after "end"
                    "end": now,
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            args, kwargs = mock_query.call_args
            # Timestamps should be inverted
            assert kwargs["start"] == now
            assert kwargs["end"] == now + 3600

    def test_tag_search_with_rfc3339_and_negative(self, tempo_toolset):
        """Test mixing RFC3339 and negative timestamps."""
        tool = SearchTagNames(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.search_tag_names_v2"
        ) as mock_search:
            mock_search.return_value = {"scopes": {}}

            result = tool._invoke(
                {
                    "start": "-3600",  # Negative (relative)
                    "end": "2024-01-01T12:00:00Z",  # RFC3339
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            args, kwargs = mock_search.call_args
            # Start should be 3600 seconds before the RFC3339 end time
            assert kwargs["start"] > 0
            assert kwargs["end"] > 0
            assert kwargs["end"] - kwargs["start"] == 3600

    def test_all_tools_handle_negative_start(self, tempo_toolset):
        """Test that all tools correctly handle negative start values."""
        test_cases = [
            (SearchTracesByQuery(tempo_toolset), {"q": "{}", "start": "-1800"}),
            (
                SearchTracesByTags(tempo_toolset),
                {"tags": "test=value", "start": "-1800"},
            ),
            (QueryTraceById(tempo_toolset), {"trace_id": "123", "start": "-1800"}),
            (SearchTagNames(tempo_toolset), {"start": "-1800"}),
            (SearchTagValues(tempo_toolset), {"tag": "service.name", "start": "-1800"}),
            (
                QueryMetricsInstant(tempo_toolset),
                {"q": "{ } | rate()", "start": "-1800"},
            ),
            (QueryMetricsRange(tempo_toolset), {"q": "{ } | rate()", "start": "-1800"}),
        ]

        for tool, params in test_cases:
            # Patch the specific API method for each tool
            api_method = tool._invoke.__qualname__.split(".")[0].lower()
            if "fetch" in api_method:
                api_method = "search_traces_by_query"
            elif "searchtracesbyquery" in api_method:
                api_method = "search_traces_by_query"
            elif "searchtracesbytags" in api_method:
                api_method = "search_traces_by_tags"
            elif "querytracebyid" in api_method:
                api_method = "query_trace_by_id_v2"
            elif "searchtagnames" in api_method:
                api_method = "search_tag_names_v2"
            elif "searchtagvalues" in api_method:
                api_method = "search_tag_values_v2"
            elif "querymetricsinstant" in api_method:
                api_method = "query_metrics_instant"
            elif "querymetricsrange" in api_method:
                api_method = "query_metrics_range"

            with patch(
                f"holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.{api_method}"
            ) as mock_method:
                mock_method.return_value = {"status": "success"}

                result = tool._invoke(params, context=create_mock_tool_invoke_context())
                assert result.status == StructuredToolResultStatus.SUCCESS

                # Verify the negative start was converted properly
                if mock_method.called:
                    args, kwargs = mock_method.call_args
                    if "start" in kwargs:
                        assert (
                            kwargs["start"] > 0
                        )  # Should be converted to positive timestamp


class TestQueryMetricsRangeWithStepAdjustment:
    """Test QueryMetricsRange with automatic step adjustment."""

    def test_metrics_range_with_no_step_auto_calculates(self, tempo_toolset):
        """Test that step is automatically calculated when not provided."""
        tool = QueryMetricsRange(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_range"
        ) as mock_query:
            mock_query.return_value = {"status": "success", "data": {}}

            # Provide a 1-hour time range
            result = tool._invoke(
                {
                    "q": "{ } | rate()",
                    "start": 1000000,
                    "end": 1003600,  # 1 hour later
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            args, kwargs = mock_query.call_args
            # With 3600 seconds and MAX_GRAPH_POINTS=100, min step = ceil(3600/100) = 36
            # The function should convert this to "36s"
            assert kwargs["step"] == "36s"

    def test_metrics_range_with_small_step_gets_adjusted(self, tempo_toolset):
        """Test that a too-small step gets adjusted to prevent too many points."""
        tool = QueryMetricsRange(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_range"
        ) as mock_query:
            mock_query.return_value = {"status": "success", "data": {}}

            # 1-day time range with 1-minute step would be 1440 points
            result = tool._invoke(
                {
                    "q": "{ } | rate()",
                    "start": 1000000,
                    "end": 1086400,  # 1 day later
                    "step": "1m",  # Too small - would create too many points
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            args, kwargs = mock_query.call_args
            # With 86400 seconds and MAX_GRAPH_POINTS=100, min step = ceil(86400/100) = 864
            # The function should adjust to "864s" = "14m24s"
            assert kwargs["step"] == "14m24s"

    def test_metrics_range_with_large_step_unchanged(self, tempo_toolset):
        """Test that a sufficiently large step is not adjusted."""
        tool = QueryMetricsRange(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_range"
        ) as mock_query:
            mock_query.return_value = {"status": "success", "data": {}}

            result = tool._invoke(
                {
                    "q": "{ } | rate()",
                    "start": 1000000,
                    "end": 1003600,  # 1 hour later
                    "step": "5m",  # 300s is larger than minimum
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            args, kwargs = mock_query.call_args
            # Step should remain "5m" since it's already large enough
            assert kwargs["step"] == "5m"

    def test_metrics_range_with_bare_number_step(self, tempo_toolset):
        """Test that bare number steps (seconds) work correctly."""
        tool = QueryMetricsRange(tempo_toolset)

        with patch(
            "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_range"
        ) as mock_query:
            mock_query.return_value = {"status": "success", "data": {}}

            result = tool._invoke(
                {
                    "q": "{ } | rate()",
                    "start": 1000000,
                    "end": 1000300,  # 5 minutes later
                    "step": "30",  # 30 seconds as bare number
                },
                context=create_mock_tool_invoke_context(),
            )

            assert result.status == StructuredToolResultStatus.SUCCESS
            args, kwargs = mock_query.call_args
            # Step should be "30s" since 30 seconds is fine for 300 second range
            assert kwargs["step"] == "30s"

    def test_metrics_range_step_adjustment_various_ranges(self, tempo_toolset):
        """Test step adjustment for various time ranges."""
        tool = QueryMetricsRange(tempo_toolset)

        test_cases = [
            # (start, end, input_step, expected_step)
            (1000000, 1000060, None, "1s"),  # 1 minute, no step -> "1s"
            (1000000, 1000060, "1", "1s"),  # 1 minute, 1s step -> "1s"
            (1000000, 1003600, "10s", "36s"),  # 1 hour, 10s step -> adjusted to 36s
            (1000000, 1604800, None, "1h40m48s"),  # 1 week, no step -> "1h40m48s"
            (
                1000000,
                1604800,
                "1h",
                "1h40m48s",
            ),  # 1 week, 1h step -> adjusted to 1h40m48s
        ]

        for start, end, input_step, expected in test_cases:
            with patch(
                "holmes.plugins.toolsets.grafana.grafana_tempo_api.GrafanaTempoAPI.query_metrics_range"
            ) as mock_query:
                mock_query.return_value = {"status": "success", "data": {}}

                params = {"q": "{ } | rate()", "start": start, "end": end}
                if input_step:
                    params["step"] = input_step

                result = tool._invoke(params, context=create_mock_tool_invoke_context())

                assert result.status == StructuredToolResultStatus.SUCCESS
                args, kwargs = mock_query.call_args
                assert (
                    kwargs["step"] == expected
                ), f"For range {end-start}s with step {input_step}, expected {expected} but got {kwargs['step']}"


class TestGrafanaTempoToolset:
    def test_toolset_has_all_tools(self):
        """Test that the toolset includes all new tools."""
        toolset = GrafanaTempoToolset()

        tool_names = [tool.name for tool in toolset.tools]

        expected_tools = [
            "tempo_search_traces_by_query",
            "tempo_search_traces_by_tags",
            "tempo_query_trace_by_id",
            "tempo_search_tag_names",
            "tempo_search_tag_values",
            "tempo_query_metrics_instant",
            "tempo_query_metrics_range",
            "tempo_fetch_traces_comparative_sample",
        ]

        for expected in expected_tools:
            assert expected in tool_names
