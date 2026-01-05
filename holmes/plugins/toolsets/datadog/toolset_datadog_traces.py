"""Datadog Traces toolset for HolmesGPT."""

import copy
import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from pydantic import AnyUrl

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.consts import STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION
from holmes.plugins.toolsets.datadog.datadog_api import (
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
    DataDogRequestError,
    execute_datadog_http_request,
    get_headers,
)
from holmes.plugins.toolsets.datadog.datadog_models import DatadogTracesConfig
from holmes.plugins.toolsets.datadog.datadog_url_utils import (
    generate_datadog_spans_analytics_url,
    generate_datadog_spans_url,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_int,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)

# Valid percentile aggregations supported by Datadog
PERCENTILE_AGGREGATIONS = ["pc75", "pc90", "pc95", "pc98", "pc99"]


class DatadogTracesToolset(Toolset):
    """Toolset for working with Datadog traces/APM data."""

    dd_config: Optional[DatadogTracesConfig] = None

    def __init__(self):
        super().__init__(
            name="datadog/traces",
            description="Toolset for interacting with Datadog APM to fetch and analyze traces",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            icon_url="https://imgix.datadoghq.com//img/about/presskit/DDlogo.jpg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                GetSpans(toolset=self),
                AggregateSpans(toolset=self),
            ],
            tags=[ToolsetTag.CORE],
        )
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions_datadog_traces.jinja2"
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        """Check prerequisites with configuration."""
        if not config:
            return False, "No configuration provided for Datadog Traces toolset"

        try:
            dd_config = DatadogTracesConfig(**config)
            self.dd_config = dd_config
            success, error_msg = self._perform_healthcheck(dd_config)
            return success, error_msg
        except Exception as e:
            logging.exception("Failed to set up Datadog traces toolset")
            return False, f"Failed to parse Datadog configuration: {str(e)}"

    def _perform_healthcheck(self, dd_config: DatadogTracesConfig) -> Tuple[bool, str]:
        """Perform health check on Datadog traces API."""
        try:
            logging.info("Performing Datadog traces configuration healthcheck...")
            headers = get_headers(dd_config)

            # The spans API uses POST, not GET
            payload = {
                "data": {
                    "type": "search_request",
                    "attributes": {
                        "filter": {
                            "from": "now-1m",
                            "to": "now",
                            "query": "*",
                            "indexes": dd_config.indexes,
                        },
                        "page": {"limit": 1},
                    },
                }
            }

            # Use search endpoint instead
            search_url = f"{dd_config.site_api_url}/api/v2/spans/events/search"

            execute_datadog_http_request(
                url=search_url,
                headers=headers,
                payload_or_params=payload,
                timeout=dd_config.request_timeout,
                method="POST",
            )

            return True, ""

        except DataDogRequestError as e:
            logging.error(
                f"Datadog API error during healthcheck: {e.status_code} - {e.response_text}"
            )
            if e.status_code == 403:
                return (
                    False,
                    "API key lacks required permissions. Make sure your API key has 'apm_read' scope.",
                )
            else:
                return False, f"Datadog API error: {e.status_code} - {e.response_text}"
        except Exception as e:
            logging.exception("Failed during Datadog traces healthcheck")
            return False, f"Healthcheck failed with exception: {str(e)}"

    def get_example_config(self) -> Dict[str, Any]:
        example_config = DatadogTracesConfig(
            dd_api_key="<your_datadog_api_key>",
            dd_app_key="<your_datadog_app_key>",
            site_api_url=AnyUrl("https://api.datadoghq.com"),
        )
        return example_config.model_dump(mode="json")


class BaseDatadogTracesTool(Tool):
    """Base class for Datadog traces tools."""

    toolset: "DatadogTracesToolset"


# Schema defines what fields to keep in compact mode
COMPACT_SCHEMA = {
    "custom": {
        "duration": True,
        "http": {"status_code": True, "host": True, "method": True, "url": True},
    },
    "status": True,
    "start_timestamp": True,
    "end_timestamp": True,
    "error": True,
    "single_span": True,
    "span_id": True,
    "trace_id": True,
    "parent_id": True,
    "service": True,
    "resource_name": True,
    "tags": {"_filter": "startswith", "_values": ["pod_name:"]},  # Generic array filter
}


class GetSpans(BaseDatadogTracesTool):
    """Tool to search for spans with specific filters."""

    def __init__(self, toolset: "DatadogTracesToolset"):
        super().__init__(
            name="fetch_datadog_spans",
            description="Search for spans in Datadog using span syntax. "
            "Supports wildcards (*) for pattern matching: @http.route:*payment*, resource_name:*user*, service:*api*. "
            "Uses the DataDog api endpoint: POST /api/v2/spans/events/search with 'query' parameter.",
            parameters={
                "query": ToolParameter(
                    description="The search query following span syntax. Supports wildcards (*) for pattern matching. Examples: @http.route:*payment*, resource_name:*user*, service:*api*. Default: *",
                    type="string",
                    required=False,
                ),
                "start_datetime": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end_datetime": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "timezone": ToolParameter(
                    description="The timezone can be specified as GMT, UTC, an offset from UTC (like UTC+1), or as a Timezone Database identifier (like America/New_York). default: UTC",
                    type="string",
                    required=False,
                ),
                "cursor": ToolParameter(
                    description="The returned paging point to use to get the next results. IMPORTANT: Cursors are single-use and stateful - never reuse the same cursor value multiple times or parallelize cursor-based calls. Each response provides a new cursor for the subsequent request.",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of spans to return. Default: 10. Warning: Using values higher than 10 may result in too much data and cause the tool call to fail.",
                    type="integer",
                    required=False,
                ),
                "sort_desc": ToolParameter(
                    description="Get the results in descending order. default: true",
                    type="boolean",
                    required=False,
                ),
                "compact": ToolParameter(
                    description="Return only essential fields to reduce output size. Use with higher limits (50-100) for initial exploration, then use compact=false with lower limits (5-10) for detailed investigation. Default: True",
                    type="boolean",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Search Spans ({params['query'] if 'query' in params else ''})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool to search spans."""
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Datadog configuration not initialized",
                params=params,
            )

        url = None
        payload: Optional[Dict[str, Any]] = None

        try:
            # Process timestamps
            from_time_int, to_time_int = process_timestamps_to_int(
                start=params.get("start_datetime"),
                end=params.get("end_datetime"),
                default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
            )

            # Convert to milliseconds for Datadog API
            from_time_ms = from_time_int * 1000
            to_time_ms = to_time_int * 1000

            query: str = params.get("query") if params.get("query") else "*"  # type: ignore
            limit = params.get("limit") if params.get("limit") else 10
            if params.get("sort") is not None:
                sort = "-timestamp" if params.get("sort") else True
            else:
                sort = "-timestamp"

            # Use POST endpoint for more complex searches
            url = f"{self.toolset.dd_config.site_api_url}/api/v2/spans/events/search"
            headers = get_headers(self.toolset.dd_config)

            payload = {
                "data": {
                    "type": "search_request",
                    "attributes": {
                        "filter": {
                            "query": query,
                            "from": str(from_time_ms),
                            "to": str(to_time_ms),
                            "indexes": self.toolset.dd_config.indexes,
                        },
                        "page": {
                            "limit": limit,
                        },
                        "sort": sort,
                    },
                }
            }

            if params.get("cursor"):
                payload["data"]["attributes"]["page"]["cursor"] = params["cursor"]

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            # Apply compact filtering if requested
            if params.get("compact", False) and "data" in response:
                response["data"] = [
                    self._filter_span_attributes(span) for span in response["data"]
                ]

            web_url = generate_datadog_spans_url(
                self.toolset.dd_config,
                query,
                from_time_ms,
                to_time_ms,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response,
                params=params,
                url=web_url,
            )

        except DataDogRequestError as e:
            logging.exception(e, exc_info=True)
            if e.status_code == 429:
                error_msg = f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
            elif e.status_code == 403:
                error_msg = (
                    f"Permission denied. Ensure your Datadog Application Key has the 'apm_read' "
                    f"permission. Error: {str(e)}"
                )
            else:
                error_msg = f"Exception while querying Datadog: {str(e)}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
                invocation=(
                    json.dumps({"url": url, "payload": payload})
                    if url and payload
                    else None
                ),
            )

        except Exception as e:
            logging.exception(e, exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
                invocation=(
                    json.dumps({"url": url, "payload": payload})
                    if url and payload
                    else None
                ),
            )

    def _apply_compact_schema(self, source: dict, schema: dict) -> dict:
        """Apply schema to filter fields from source dict."""
        result: Dict[str, Any] = {}

        for key, value in schema.items():
            if key not in source:
                continue

            source_value = source[key]

            if isinstance(value, dict):
                # Check if it's a filter directive for arrays
                if "_filter" in value and isinstance(source_value, list):
                    filter_type = value["_filter"]
                    filter_values = value.get("_values", [])

                    if filter_type == "startswith":
                        # Filter array items that start with any of the specified values
                        filtered = [
                            item
                            for item in source_value
                            if isinstance(item, str)
                            and any(item.startswith(prefix) for prefix in filter_values)
                        ]
                        if filtered:
                            result[key] = filtered

                elif isinstance(source_value, dict):
                    # Regular nested object - recurse
                    nested_result = self._apply_compact_schema(source_value, value)
                    if nested_result:
                        result[key] = nested_result

            elif value is True:
                # Copy the field as-is
                result[key] = source_value

        return result

    def _filter_span_attributes(self, span: dict) -> dict:
        """Filter span to include only essential fields."""
        filtered_span = {
            "id": span.get("id"),
            "type": span.get("type"),
        }

        if "attributes" in span:
            filtered_span["attributes"] = self._apply_compact_schema(
                span["attributes"], COMPACT_SCHEMA
            )

        return filtered_span


class AggregateSpans(BaseDatadogTracesTool):
    """Tool to aggregate span data into buckets and compute metrics and timeseries."""

    def __init__(self, toolset: "DatadogTracesToolset"):
        super().__init__(
            name="aggregate_datadog_spans",
            description="Aggregate spans into buckets and compute metrics and timeseries. "
            "Uses the DataDog api endpoint: POST /api/v2/spans/analytics/aggregate",
            parameters={
                "query": ToolParameter(
                    description="Search query following span syntax. Default: '*'",
                    type="string",
                    required=False,
                ),
                "start_datetime": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end_datetime": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "compute": ToolParameter(
                    description="List of metrics to compute from the matching spans. Supports up to 10 computes at the same time.",
                    type="array",
                    required=True,
                    items=ToolParameter(
                        type="object",
                        properties={
                            "aggregation": ToolParameter(
                                type="string",
                                required=True,
                                enum=[
                                    "count",
                                    "cardinality",
                                    "sum",
                                    "min",
                                    "max",
                                    "avg",
                                    "median",
                                ]
                                + PERCENTILE_AGGREGATIONS,
                                description="The aggregation method.",
                            ),
                            "metric": ToolParameter(
                                type="string",
                                required=False,
                                description="The span attribute to aggregate. Required for all non-count aggregations",
                            ),
                            "type": ToolParameter(
                                type="string",
                                required=False,
                                enum=["total", "timeseries"],
                                description="Compute type for the aggregation. Default: 'total'",
                            ),
                            "interval": ToolParameter(
                                type="string",
                                required=False,
                                description="The time buckets for timeseries results (e.g., '5m', '1h'). The time buckets' size (only used for type=timeseries) Defaults to a resolution of 150 points.",
                            ),
                        },
                    ),
                ),
                "group_by": ToolParameter(
                    description="List of facets to split the aggregate data by",
                    type="array",
                    required=False,
                    items=ToolParameter(
                        type="object",
                        properties={
                            "facet": ToolParameter(
                                type="string",
                                required=True,
                                description="The span attribute to split by",
                            ),
                            "limit": ToolParameter(
                                type="integer",
                                required=False,
                                description="Maximum number of facet groups to return. Default: 10",
                            ),
                            "missing": ToolParameter(
                                type="string",
                                required=False,
                                description="The value to use for spans that don't have the facet",
                            ),
                            "sort": ToolParameter(
                                type="object",
                                required=False,
                                description="Sort configuration for the groups",
                                properties={
                                    # Not working correctly
                                    # "aggregation": ToolParameter(
                                    #     type="string",
                                    #     required=True,
                                    #     description="The aggregation method to sort by",
                                    # ),
                                    "metric": ToolParameter(
                                        type="string",
                                        required=False,
                                        description="The metric to sort by when using a metric aggregation. (only used for type=measure).",
                                    ),
                                    "type": ToolParameter(
                                        type="string",
                                        required=False,
                                        enum=["alphabetical", "measure"],
                                        description="The type of sorting to use",
                                    ),
                                    "order": ToolParameter(
                                        type="string",
                                        required=False,
                                        enum=["asc", "desc"],
                                        description="The sort order. Default: 'desc'",
                                    ),
                                },
                            ),
                            "total": ToolParameter(
                                type="boolean",
                                required=False,
                                description="Whether to include a 'total' group with all non-faceted results",
                            ),
                            "histogram": ToolParameter(
                                type="object",
                                required=False,
                                description="Histogram configuration for numeric facets",
                                properties={
                                    "interval": ToolParameter(
                                        type="number",
                                        required=True,
                                        description="The bin size for the histogram",
                                    ),
                                    "min": ToolParameter(
                                        type="number",
                                        required=False,
                                        description="The minimum value for the histogram",
                                    ),
                                    "max": ToolParameter(
                                        type="number",
                                        required=False,
                                        description="The maximum value for the histogram",
                                    ),
                                },
                            ),
                        },
                    ),
                ),
                "timezone": ToolParameter(
                    description="The timezone for time-based results (e.g., 'GMT', 'UTC', 'America/New_York'). Default: 'UTC'",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        query = params.get("query", "*")
        compute_info = ""
        if params.get("compute"):
            aggregations = [c.get("aggregation", "") for c in params["compute"]]
            compute_info = f" (computing: {', '.join(aggregations)})"
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Aggregate Spans ({query}){compute_info}"

    def _fix_percentile_aggregations(self, compute_params: list) -> list:
        """Fix common percentile format mistakes that the LLM makes when choosing from the enum (e.g., p95 -> pc95).

        Args:
            compute_params: List of compute parameter dictionaries

        Returns:
            List of compute parameters with corrected aggregation values
        """
        # Deep copy the entire compute params to avoid modifying the original
        processed_compute = copy.deepcopy(compute_params)

        # Simple replacement for each known percentile
        for compute_item in processed_compute:
            if isinstance(compute_item, dict) and "aggregation" in compute_item:
                agg_value = compute_item["aggregation"]
                # Check if it matches p\d\d pattern (e.g., p95)
                if re.match(r"^p\d{2}$", agg_value):
                    # Convert to pc format and check if it's valid
                    pc_version = "pc" + agg_value[1:]
                    if pc_version in PERCENTILE_AGGREGATIONS:
                        compute_item["aggregation"] = pc_version

        return processed_compute

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool to aggregate spans."""
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Datadog configuration not initialized",
                params=params,
            )

        url = None
        payload = None

        try:
            # Process timestamps
            from_time_int, to_time_int = process_timestamps_to_int(
                start=params.get("start_datetime"),
                end=params.get("end_datetime"),
                default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
            )

            # Convert to milliseconds for Datadog API
            from_time_ms = from_time_int * 1000
            to_time_ms = to_time_int * 1000

            query = params.get("query", "*")

            # Build the request payload
            url = f"{self.toolset.dd_config.site_api_url}/api/v2/spans/analytics/aggregate"
            headers = get_headers(self.toolset.dd_config)

            # Build payload attributes first
            # Process compute parameter to fix common p95->pc95 style mistakes
            compute_params = params.get("compute", [])
            processed_compute = self._fix_percentile_aggregations(compute_params)

            attributes: Dict[str, Any] = {
                "filter": {
                    "query": query,
                    "from": str(from_time_ms),
                    "to": str(to_time_ms),
                },
                "compute": processed_compute,
            }

            # Add optional fields
            if params.get("group_by"):
                attributes["group_by"] = params["group_by"]

            # Add options if timezone is specified
            options: Dict[str, Any] = {}
            if params.get("timezone"):
                options["timezone"] = params["timezone"]

            if options:
                attributes["options"] = options

            payload = {
                "data": {
                    "type": "aggregate_request",
                    "attributes": attributes,
                }
            }

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            web_url = generate_datadog_spans_analytics_url(
                self.toolset.dd_config,
                query,
                from_time_ms,
                to_time_ms,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response,
                params=params,
                url=web_url,
            )

        except DataDogRequestError as e:
            logging.exception(e, exc_info=True)
            if e.status_code == 429:
                error_msg = f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
            elif e.status_code == 403:
                error_msg = (
                    f"Permission denied. Ensure your Datadog Application Key has the 'apm_read' "
                    f"permission. Error: {str(e)}"
                )
            else:
                error_msg = f"Exception while querying Datadog: {str(e)}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
                invocation=(
                    json.dumps({"url": url, "payload": payload})
                    if url and payload
                    else None
                ),
            )

        except Exception as e:
            logging.exception(e, exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
                invocation=(
                    json.dumps({"url": url, "payload": payload})
                    if url and payload
                    else None
                ),
            )
