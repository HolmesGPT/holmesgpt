import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, cast
from urllib.parse import quote

from holmes.common.env_vars import MAX_GRAPH_POINTS, load_bool
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.consts import STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION
from holmes.plugins.toolsets.grafana.base_grafana_toolset import BaseGrafanaToolset
from holmes.plugins.toolsets.grafana.common import (
    GrafanaTempoConfig,
)
from holmes.plugins.toolsets.grafana.grafana_tempo_api import GrafanaTempoAPI
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_GRAPH_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.utils import (
    adjust_step_for_max_points,
    duration_string_to_seconds,
    process_timestamps_to_int,
    seconds_to_duration_string,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)

TEMPO_LABELS_ADD_PREFIX = load_bool("TEMPO_LABELS_ADD_PREFIX", True)


def _build_grafana_explore_tempo_url(
    config: GrafanaTempoConfig,
    query: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    limit: int = 20,
    trace_id: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[str] = None,
) -> Optional[str]:
    if not config.grafana_datasource_uid:
        return None
    try:
        base_url = config.external_url or config.url
        datasource_uid = config.grafana_datasource_uid
        now_s = int(time.time())
        start_ts = start if start else now_s - 3600
        end_ts = end if end else now_s
        start_delta = max(0, now_s - start_ts)
        end_delta = max(0, now_s - end_ts)
        from_str = f"now-{start_delta}s" if start_delta > 0 else "now-1h"
        to_str = "now" if end_delta == 0 else f"now-{end_delta}s"
        pane_id = "tmp"

        if trace_id:
            # Direct trace ID lookup - query is just the traceID string
            query_obj = {
                "refId": "A",
                "datasource": {"type": "tempo", "uid": datasource_uid},
                "queryType": "traceql",
                "limit": limit,
                "tableType": "traces",
                "metricsQueryType": "range",
                "query": trace_id,
            }
        elif tags:
            # Build filters from tag name
            scope = "resource" if tags.startswith("resource.") else "span"
            filter_id = str(uuid.uuid4())[:8]
            filters = [
                {
                    "id": filter_id,
                    "operator": "=",
                    "scope": scope,
                    "tag": tags,
                    "value": [],
                }
            ]
            query_obj = {
                "refId": "A",
                "datasource": {"type": "tempo", "uid": datasource_uid},
                "queryType": "traceqlSearch",
                "limit": limit,
                "tableType": "traces",
                "metricsQueryType": "range",
                "query": "",
                "filters": filters,
            }
        elif filters:
            # Tag filters - use traceqlSearch with filters array
            query_obj = {
                "refId": "A",
                "datasource": {"type": "tempo", "uid": datasource_uid},
                "queryType": "traceqlSearch",
                "limit": limit,
                "tableType": "traces",
                "metricsQueryType": "range",
                "query": "",
                "filters": filters,
            }
        else:
            # Regular TraceQL query
            safe_query = query if query else "{}"
            query_obj = {
                "refId": "A",
                "datasource": {"type": "tempo", "uid": datasource_uid},
                "queryType": "traceql",
                "limit": limit,
                "tableType": "traces",
                "metricsQueryType": "range",
                "query": safe_query,
            }

        panes = {
            pane_id: {
                "datasource": datasource_uid,
                "queries": [query_obj],
                "range": {"from": from_str, "to": to_str},
            }
        }

        panes_encoded = quote(
            json.dumps(panes, separators=(",", ":"), ensure_ascii=False), safe=""
        )
        return f"{base_url}/explore?schemaVersion=1&panes={panes_encoded}&orgId=1"
    except Exception:
        return None


class BaseGrafanaTempoToolset(BaseGrafanaToolset):
    config_class = GrafanaTempoConfig

    def get_example_config(self):
        example_config = GrafanaTempoConfig(
            api_key="YOUR API KEY",
            url="YOUR GRAFANA URL",
            grafana_datasource_uid="<UID of the tempo datasource to use>",
        )
        return example_config.model_dump()

    @property
    def grafana_config(self) -> GrafanaTempoConfig:
        return cast(GrafanaTempoConfig, self._grafana_config)

    def health_check(self) -> Tuple[bool, str]:
        """Test a dummy query to check if service available."""
        try:
            _ = GrafanaTempoAPI(self.grafana_config).search_traces_by_query(
                q='{ .service.name = "test-endpoint" }',
                limit=1,
            )
        except Exception as e:
            return False, f"Unable to connect to Tempo.\n{str(e)}"

        return True, ""

    def build_k8s_filters(
        self, params: Dict[str, Any], use_exact_match: bool
    ) -> List[str]:
        """Build TraceQL filters for k8s parameters.

        Args:
            params: Dictionary containing k8s parameters
            use_exact_match: If True, uses exact match (=), if False uses regex match (=~)

        Returns:
            List of TraceQL filter strings
        """
        prefix = ""
        if TEMPO_LABELS_ADD_PREFIX:
            prefix = "resource."

        filters = []
        labels = self.grafana_config.labels

        # Define parameter mappings: (param_name, label_attribute)
        parameter_mappings = [
            ("service_name", "service"),
            ("pod_name", "pod"),
            ("namespace_name", "namespace"),
            ("deployment_name", "deployment"),
            ("node_name", "node"),
        ]

        for param_name, label_attr in parameter_mappings:
            value = params.get(param_name)
            if value:
                # Get the label from the config
                label = getattr(labels, label_attr)

                # Build the filter based on match type
                if use_exact_match:
                    # Escape double quotes in the value for exact match
                    escaped_value = value.replace('"', '\\"')
                    filters.append(f'{prefix}{label}="{escaped_value}"')
                else:
                    # For partial match, use simple substring matching
                    # Don't escape anything - let Tempo handle the regex
                    filters.append(f'{prefix}{label}=~".*{value}.*"')

        return filters

    @staticmethod
    def adjust_start_end_time(params: Dict) -> Tuple[int, int]:
        return process_timestamps_to_int(
            start=params.get("start"),
            end=params.get("end"),
            default_time_span_seconds=DEFAULT_GRAPH_TIME_SPAN_SECONDS,
        )


class FetchTracesSimpleComparison(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_fetch_traces_comparative_sample",
            description="""Fetches statistics and representative samples of fast, slow, and typical traces for performance analysis. Requires either a `base_query` OR at least one of `service_name`, `pod_name`, `namespace_name`, `deployment_name`, `node_name`.

Important: call this tool first when investigating performance issues via traces. This tool provides comprehensive analysis for identifying patterns.

Examples:
- For service latency: service_name="payment" (matches "payment-service" too)
- For namespace issues: namespace_name="production"
- Combined: service_name="auth", namespace_name="staging\"""",
            parameters={
                "service_name": ToolParameter(
                    description="Service to analyze (partial match supported)",
                    type="string",
                    required=False,
                ),
                "pod_name": ToolParameter(
                    description="Filter traces by pod name (partial match supported)",
                    type="string",
                    required=False,
                ),
                "namespace_name": ToolParameter(
                    description="Kubernetes namespace to filter traces",
                    type="string",
                    required=False,
                ),
                "deployment_name": ToolParameter(
                    description="Filter traces by deployment name (partial match supported)",
                    type="string",
                    required=False,
                ),
                "node_name": ToolParameter(
                    description="Filter traces by node name",
                    type="string",
                    required=False,
                ),
                "base_query": ToolParameter(
                    description=(
                        "Custom TraceQL filter. Supports span/resource attributes, "
                        "duration, and aggregates (count(), avg(), min(), max(), sum()). "
                        "Examples: '{span.http.status_code>=400}', '{duration>100ms}'"
                    ),
                    type="string",
                    required=False,
                ),
                "sample_count": ToolParameter(
                    description="Number of traces to fetch from each category (fastest/slowest). Default 3",
                    type="integer",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    @staticmethod
    def validate_params(params: Dict[str, Any], expected_params: List[str]):
        for param in expected_params:
            if param in params and params[param] not in (None, "", [], {}):
                return None

        return f"At least one of the following argument is expected but none were set: {expected_params}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            # Build query
            if params.get("base_query"):
                base_query = params["base_query"]
            else:
                # Use the shared utility with partial matching (regex)
                filters = self._toolset.build_k8s_filters(params, use_exact_match=False)

                # Validate that at least one parameter was provided
                invalid_params_error = FetchTracesSimpleComparison.validate_params(
                    params,
                    [
                        "service_name",
                        "pod_name",
                        "namespace_name",
                        "deployment_name",
                        "node_name",
                    ],
                )
                if invalid_params_error:
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=invalid_params_error,
                        params=params,
                    )

                base_query = " && ".join(filters)

            sample_count = params.get("sample_count", 3)

            start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

            # Create API instance
            api = GrafanaTempoAPI(self._toolset.grafana_config)

            # Step 1: Get all trace summaries
            stats_query = f"{{{base_query}}}"

            # Debug log the query (useful for troubleshooting)
            import logging

            logger = logging.getLogger(__name__)
            logger.debug(f"Tempo query: {stats_query}")

            logger.debug(f"start: {start}, end: {end}")

            all_traces_response = api.search_traces_by_query(
                q=stats_query,
                start=start,
                end=end,
                limit=1000,
            )

            logger.debug(f"Response: {all_traces_response}")

            traces = all_traces_response.get("traces", [])
            if not traces:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data="No traces found matching the query",
                    params=params,
                )

            # Step 2: Sort traces by duration
            sorted_traces = sorted(traces, key=lambda x: x.get("durationMs", 0))

            # Step 3: Calculate basic statistics
            durations = [t.get("durationMs", 0) for t in sorted_traces]
            stats = {
                "trace_count": len(durations),
                "min_ms": durations[0],
                "p25_ms": durations[len(durations) // 4]
                if len(durations) >= 4
                else durations[0],
                "p50_ms": durations[len(durations) // 2],
                "p75_ms": durations[3 * len(durations) // 4]
                if len(durations) >= 4
                else durations[-1],
                "p90_ms": durations[int(len(durations) * 0.9)]
                if len(durations) >= 10
                else durations[-1],
                "p99_ms": durations[int(len(durations) * 0.99)]
                if len(durations) >= 100
                else durations[-1],
                "max_ms": durations[-1],
            }

            # Step 4: Select representative traces to fetch
            fastest_indices = list(range(min(sample_count, len(sorted_traces))))
            slowest_indices = list(
                range(max(0, len(sorted_traces) - sample_count), len(sorted_traces))
            )

            # Add median trace
            median_idx = len(sorted_traces) // 2

            # Step 5: Fetch full trace details
            def fetch_full_trace(trace_summary):
                trace_id = trace_summary.get("traceID")
                if not trace_id:
                    return None

                try:
                    start_nano = trace_summary.get("startTimeUnixNano")
                    trace_start = (
                        int(int(start_nano) / 1_000_000_000) if start_nano else None
                    )

                    trace_data = api.query_trace_by_id_v2(
                        trace_id=trace_id, start=trace_start
                    )
                    return {
                        "traceID": trace_id,
                        "durationMs": trace_summary.get("durationMs", 0),
                        "rootServiceName": trace_summary.get(
                            "rootServiceName", "unknown"
                        ),
                        "traceData": trace_data,  # Raw trace data
                    }
                except Exception as e:
                    error_msg = f"Failed to fetch full trace: {str(e)}"
                    return {
                        "traceID": trace_id,
                        "durationMs": trace_summary.get("durationMs", 0),
                        "error": error_msg,
                    }

            # Fetch the selected traces
            result = {
                "statistics": stats,
                "fastest_traces": [
                    fetch_full_trace(sorted_traces[i]) for i in fastest_indices
                ],
                "median_trace": fetch_full_trace(sorted_traces[median_idx]),
                "slowest_traces": [
                    fetch_full_trace(sorted_traces[i]) for i in slowest_indices
                ],
            }

            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                query=f"{{{base_query}}}",
                start=start,
                end=end,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
                url=explore_url,
            )

        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Error fetching traces: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Simple Tempo Traces Comparison"


class SearchTracesByQuery(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_search_traces_by_query",
            description=(
                "Search for traces using TraceQL query language. "
                "Uses the Tempo API endpoint: GET /api/search with 'q' parameter.\n\n"
                "TraceQL can select traces based on:\n"
                "- Span and resource attributes\n"
                "- Timing and duration\n"
                "- Aggregate functions:\n"
                "  • count() - Count number of spans\n"
                "  • avg(attribute) - Calculate average\n"
                "  • min(attribute) - Find minimum value\n"
                "  • max(attribute) - Find maximum value\n"
                "  • sum(attribute) - Sum values\n\n"
                "Examples:\n"
                '- Specific operation: {resource.service.name = "frontend" && name = "POST /api/orders"}\n'
                '- Error traces: {resource.service.name="frontend" && name = "POST /api/orders" && status = error}\n'
                '- HTTP errors: {resource.service.name="frontend" && name = "POST /api/orders" && span.http.status_code >= 500}\n'
                '- Multi-service: {span.service.name="frontend" && name = "GET /api/products/{id}"} && {span.db.system="postgresql"}\n'
                "- With aggregates: { status = error } | by(resource.service.name) | count() > 1"
            ),
            parameters={
                "q": ToolParameter(
                    description=(
                        "TraceQL query. Supports filtering by span/resource attributes, "
                        "duration, and aggregate functions (count(), avg(), min(), max(), sum()). "
                        "Examples: '{resource.service.name = \"frontend\"}', "
                        '\'{resource.service.name="frontend" && name = "POST /api/orders" && status = error}\', '
                        '\'{resource.service.name="frontend" && name = "POST /api/orders" && span.http.status_code >= 500}\', '
                        "'{} | count() > 10'"
                    ),
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description="Maximum number of traces to return",
                    type="integer",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "spss": ToolParameter(
                    description="Spans per span set",
                    type="integer",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = GrafanaTempoAPI(self._toolset.grafana_config)

        start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

        try:
            result = api.search_traces_by_query(
                q=params["q"],
                limit=params.get("limit"),
                start=start,
                end=end,
                spss=params.get("spss"),
            )

            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                query=params["q"],
                start=start,
                end=end,
                limit=params.get("limit") or 20,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
                url=explore_url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Searched traces with TraceQL"


class SearchTracesByTags(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_search_traces_by_tags",
            description=(
                "Search for traces using logfmt-encoded tags. "
                "Uses the Tempo API endpoint: GET /api/search with 'tags' parameter. "
                'Example: service.name="api" http.status_code="500"'
            ),
            parameters={
                "tags": ToolParameter(
                    description='Logfmt-encoded span/process attributes (e.g., \'service.name="api" http.status_code="500"\')',
                    type="string",
                    required=True,
                ),
                "min_duration": ToolParameter(
                    description="Minimum trace duration (e.g., '5s', '100ms')",
                    type="string",
                    required=False,
                ),
                "max_duration": ToolParameter(
                    description="Maximum trace duration (e.g., '10s', '1000ms')",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of traces to return",
                    type="integer",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "spss": ToolParameter(
                    description="Spans per span set",
                    type="integer",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = GrafanaTempoAPI(self._toolset.grafana_config)

        start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

        try:
            result = api.search_traces_by_tags(
                tags=params["tags"],
                min_duration=params.get("min_duration"),
                max_duration=params.get("max_duration"),
                limit=params.get("limit"),
                start=start,
                end=end,
                spss=params.get("spss"),
            )

            tag_filters = params["tags"].replace(" ", " && ")
            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                query=f"{{{tag_filters}}}",
                start=start,
                end=end,
                limit=params.get("limit") or 20,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
                url=explore_url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Searched traces with tags"


class QueryTraceById(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_query_trace_by_id",
            description=(
                "Retrieve detailed trace information by trace ID. "
                "Uses the Tempo API endpoint: GET /api/v2/traces/{trace_id}. "
                "Returns the full trace data in OpenTelemetry format."
            ),
            parameters={
                "trace_id": ToolParameter(
                    description="The unique trace ID to fetch",
                    type="string",
                    required=True,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = GrafanaTempoAPI(self._toolset.grafana_config)

        start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

        try:
            trace_data = api.query_trace_by_id_v2(
                trace_id=params["trace_id"],
                start=start,
                end=end,
            )

            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                trace_id=params["trace_id"],
                start=start,
                end=end,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=trace_data,
                params=params,
                url=explore_url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Retrieved trace {params.get('trace_id')}"


class SearchTagNames(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_search_tag_names",
            description=(
                "Discover available tag names across traces. "
                "Uses the Tempo API endpoint: GET /api/v2/search/tags. "
                "Returns tags organized by scope (resource, span, intrinsic)."
            ),
            parameters={
                "scope": ToolParameter(
                    description="Filter by scope: 'resource', 'span', or 'intrinsic'",
                    type="string",
                    required=False,
                ),
                "q": ToolParameter(
                    description="TraceQL query to filter tags (e.g., '{resource.cluster=\"us-east-1\"}')",
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of tag names to return",
                    type="integer",
                    required=False,
                ),
                "max_stale_values": ToolParameter(
                    description="Maximum stale values parameter",
                    type="integer",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = GrafanaTempoAPI(self._toolset.grafana_config)

        start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

        try:
            result = api.search_tag_names_v2(
                scope=params.get("scope"),
                q=params.get("q"),
                start=start,
                end=end,
                limit=params.get("limit"),
                max_stale_values=params.get("max_stale_values"),
            )

            query_filter = params.get("q") or "{}"
            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                query=query_filter,
                start=start,
                end=end,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
                url=explore_url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Discovered tag names"


class SearchTagValues(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_search_tag_values",
            description=(
                "Get all values for a specific tag. "
                "Uses the Tempo API endpoint: GET /api/v2/search/tag/{tag}/values. "
                "Useful for discovering what values exist for a given tag."
            ),
            parameters={
                "tag": ToolParameter(
                    description="The tag name to get values for (e.g., 'resource.service.name', 'http.status_code')",
                    type="string",
                    required=True,
                ),
                "q": ToolParameter(
                    description="TraceQL query to filter tag values (e.g., '{resource.cluster=\"us-east-1\"}')",
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of values to return",
                    type="integer",
                    required=False,
                ),
                "max_stale_values": ToolParameter(
                    description="Maximum stale values parameter",
                    type="integer",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = GrafanaTempoAPI(self._toolset.grafana_config)

        start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

        try:
            result = api.search_tag_values_v2(
                tag=params["tag"],
                q=params.get("q"),
                start=start,
                end=end,
                limit=params.get("limit"),
                max_stale_values=params.get("max_stale_values"),
            )

            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                start=start,
                end=end,
                tags=params["tag"],
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
                url=explore_url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Retrieved values for tag '{params.get('tag')}'"


class QueryMetricsInstant(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_query_metrics_instant",
            description=(
                "Compute a single TraceQL metric value across time range. "
                "Uses the Tempo API endpoint: GET /api/metrics/query. "
                "TraceQL metrics compute aggregated metrics from trace data. "
                "Returns a single value for the entire time range. "
                "Basic syntax: {selector} | function(attribute) [by (grouping)]\n\n"
                "TraceQL metrics can help answer questions like:\n"
                "- How many database calls across all systems are downstream of your application?\n"
                "- What services beneath a given endpoint are failing?\n"
                "- What services beneath an endpoint are slow?\n\n"
                "TraceQL metrics help you answer these questions by parsing your traces in aggregate. "
                "The instant version returns a single value for the query and is preferred over "
                "query_metrics_range when you don't need the granularity of a full time-series but want "
                "a total sum or single value computed across the whole time range."
            ),
            parameters={
                "q": ToolParameter(
                    description=(
                        "TraceQL metrics query. Supported functions: rate, count_over_time, "
                        "sum_over_time, max_over_time, min_over_time, avg_over_time, "
                        "quantile_over_time, histogram_over_time, compare. "
                        "Can use topk or bottomk modifiers. "
                        "Syntax: {selector} | function(attribute) [by (grouping)]. "
                        'Example: {resource.service.name="api"} | avg_over_time(duration)'
                    ),
                    type="string",
                    required=True,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = GrafanaTempoAPI(self._toolset.grafana_config)

        start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

        try:
            result = api.query_metrics_instant(
                q=params["q"],
                start=start,
                end=end,
            )

            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                query=params["q"],
                start=start,
                end=end,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
                url=explore_url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: Computed TraceQL metric"
        )


class QueryMetricsRange(Tool):
    def __init__(self, toolset: BaseGrafanaTempoToolset):
        super().__init__(
            name="tempo_query_metrics_range",
            description=(
                "Get time series data from TraceQL metrics queries. "
                "Uses the Tempo API endpoint: GET /api/metrics/query_range. "
                "Returns metrics computed at regular intervals (controlled by 'step' parameter). "
                "Use this for graphing metrics over time or analyzing trends. "
                "Basic syntax: {selector} | function(attribute) [by (grouping)]\n\n"
                "TraceQL metrics can help answer questions like:\n"
                "- How many database calls across all systems are downstream of your application?\n"
                "- What services beneath a given endpoint are failing?\n"
                "- What services beneath an endpoint are slow?\n\n"
                "TraceQL metrics help you answer these questions by parsing your traces in aggregate."
            ),
            parameters={
                "q": ToolParameter(
                    description=(
                        "TraceQL metrics query. Supported functions: rate, count_over_time, "
                        "sum_over_time, max_over_time, min_over_time, avg_over_time, "
                        "quantile_over_time, histogram_over_time, compare. "
                        "Can use topk or bottomk modifiers. "
                        "Syntax: {selector} | function(attribute) [by (grouping)]. "
                        'Example: {resource.service.name="api"} | avg_over_time(duration)'
                    ),
                    type="string",
                    required=True,
                ),
                "step": ToolParameter(
                    description="Time series granularity (e.g., '1m', '5m', '1h')",
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_GRAPH_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "exemplars": ToolParameter(
                    description="Maximum number of exemplars to return",
                    type="integer",
                    required=False,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: Dict, context: ToolInvokeContext) -> StructuredToolResult:
        api = GrafanaTempoAPI(self._toolset.grafana_config)

        start, end = BaseGrafanaTempoToolset.adjust_start_end_time(params)

        # Calculate appropriate step
        step_param = params.get("step")
        step_seconds = duration_string_to_seconds(step_param) if step_param else None
        adjusted_step = adjust_step_for_max_points(
            end - start,
            int(MAX_GRAPH_POINTS),
            step_seconds,
        )
        step = seconds_to_duration_string(adjusted_step)

        try:
            result = api.query_metrics_range(
                q=params["q"],
                step=step,
                start=start,
                end=end,
                exemplars=params.get("exemplars"),
            )

            explore_url = _build_grafana_explore_tempo_url(
                self._toolset.grafana_config,
                query=params["q"],
                start=start,
                end=end,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=result,
                params=params,
                url=explore_url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Retrieved TraceQL metrics time series"


class GrafanaTempoToolset(BaseGrafanaTempoToolset):
    def __init__(self):
        super().__init__(
            name="grafana/tempo",
            description="Fetches kubernetes traces from Tempo",
            icon_url="https://grafana.com/static/assets/img/blog/tempo.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/grafanatempo/",
            tools=[
                FetchTracesSimpleComparison(self),
                SearchTracesByQuery(self),
                SearchTracesByTags(self),
                QueryTraceById(self),
                SearchTagNames(self),
                SearchTagValues(self),
                QueryMetricsInstant(self),
                QueryMetricsRange(self),
            ],
        )
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "toolset_grafana_tempo.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")
