"""Datadog Traces toolset for HolmesGPT."""

import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from holmes.core.tools import (
    CallablePrerequisite,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolsetTag,
)
from holmes.plugins.toolsets.datadog.datadog_api import (
    DataDogRequestError,
    DatadogBaseConfig,
    execute_datadog_http_request,
    get_headers,
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
)
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_int,
    toolset_name_for_one_liner,
)
from holmes.plugins.toolsets.datadog.datadog_traces_formatter import (
    format_traces_list,
    format_trace_hierarchy,
    format_spans_search,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_TIME_SPAN_SECONDS,
)


class DatadogTracesConfig(DatadogBaseConfig):
    indexes: list[str] = ["*"]


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
                FetchDatadogTracesList(toolset=self),
                FetchDatadogTraceById(toolset=self),
                FetchDatadogSpansByFilter(toolset=self),
            ],
            tags=[ToolsetTag.CORE],
        )
        self._reload_instructions()

    def _reload_instructions(self):
        """Load Datadog traces specific troubleshooting instructions."""
        template_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "instructions_datadog_traces.jinja2"
            )
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

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
        """Get example configuration for this toolset."""
        return {
            "dd_api_key": "<your_datadog_api_key>",
            "dd_app_key": "<your_datadog_app_key>",
            "site_api_url": "https://api.datadoghq.com",  # or https://api.datadoghq.eu for EU
            "request_timeout": 60,
        }


class BaseDatadogTracesTool(Tool):
    """Base class for Datadog traces tools."""

    toolset: "DatadogTracesToolset"


class FetchDatadogTracesList(BaseDatadogTracesTool):
    """Tool to fetch a list of traces from Datadog."""

    def __init__(self, toolset: "DatadogTracesToolset"):
        super().__init__(
            name="fetch_datadog_traces",
            description="[datadog/traces toolset] Fetch a list of traces from Datadog with optional filters",
            parameters={
                "service": ToolParameter(
                    description="Filter by service name",
                    type="string",
                    required=False,
                ),
                "operation": ToolParameter(
                    description="Filter by operation name",
                    type="string",
                    required=False,
                ),
                "resource": ToolParameter(
                    description="Filter by resource name",
                    type="string",
                    required=False,
                ),
                "min_duration": ToolParameter(
                    description="Minimum duration (e.g., '5s', '500ms', '1m')",
                    type="string",
                    required=False,
                ),
                "start_datetime": ToolParameter(
                    description="Start time in RFC3339 format or relative time in seconds (negative for past)",
                    type="string",
                    required=False,
                ),
                "end_datetime": ToolParameter(
                    description="End time in RFC3339 format or relative time in seconds (negative for past)",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of traces to return",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        filters = []
        if "service" in params:
            filters.append(f"service={params['service']}")
        if "operation" in params:
            filters.append(f"operation={params['operation']}")
        if "min_duration" in params:
            filters.append(f"duration>{params['min_duration']}")

        filter_str = ", ".join(filters) if filters else "all"
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Traces ({filter_str})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool to fetch traces."""
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

            # Build search query
            query_parts = []

            if params.get("service"):
                query_parts.append(f"service:{params['service']}")

            if params.get("operation"):
                query_parts.append(f"operation_name:{params['operation']}")

            if params.get("resource"):
                query_parts.append(f"resource_name:{params['resource']}")

            if params.get("min_duration"):
                # Parse duration string (e.g., "5s", "500ms", "1m")
                duration_str = params["min_duration"].lower()
                if duration_str.endswith("ms"):
                    duration_ns = int(float(duration_str[:-2]) * 1_000_000)
                elif duration_str.endswith("s"):
                    duration_ns = int(float(duration_str[:-1]) * 1_000_000_000)
                elif duration_str.endswith("m"):
                    duration_ns = int(float(duration_str[:-1]) * 60 * 1_000_000_000)
                else:
                    # Assume milliseconds if no unit
                    duration_ns = int(float(duration_str) * 1_000_000)

                query_parts.append(f"@duration:>{duration_ns}")

            query = " ".join(query_parts) if query_parts else "*"

            # Prepare API request - use POST search endpoint
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
                        "page": {"limit": params.get("limit", 50)},
                        "sort": "-timestamp",
                    },
                }
            }

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            # Handle tuple response from POST requests
            if isinstance(response, tuple):
                spans, _ = response
            elif response:
                spans = response.get("data", [])
            else:
                spans = []

            # Format the traces using the formatter
            formatted_output = format_traces_list(spans, limit=params.get("limit", 50))
            if not formatted_output:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params=params,
                    data="No matching traces found.",
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=formatted_output,
                params=params,
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


class FetchDatadogTraceById(BaseDatadogTracesTool):
    """Tool to fetch detailed information about a specific trace."""

    def __init__(self, toolset: "DatadogTracesToolset"):
        super().__init__(
            name="fetch_datadog_trace_by_id",
            description="[datadog/traces toolset] Fetch detailed information about a specific trace by its ID",
            parameters={
                "trace_id": ToolParameter(
                    description="The trace ID to fetch details for",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        trace_id = params.get("trace_id", "unknown")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Trace Details ({trace_id})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool to fetch trace details."""
        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Datadog configuration not initialized",
                params=params,
            )

        trace_id = params.get("trace_id")
        if not trace_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="trace_id parameter is required",
                params=params,
            )

        url = None
        payload = None

        try:
            # For Datadog, we need to search for all spans with the given trace_id
            # Using a reasonable time window (last 7 days by default)
            current_time = int(time.time())
            from_time_ms = (current_time - 604800) * 1000  # 7 days ago
            to_time_ms = current_time * 1000

            url = f"{self.toolset.dd_config.site_api_url}/api/v2/spans/events/search"
            headers = get_headers(self.toolset.dd_config)

            payload = {
                "data": {
                    "type": "search_request",
                    "attributes": {
                        "filter": {
                            "query": f"trace_id:{trace_id}",
                            "from": str(from_time_ms),
                            "to": str(to_time_ms),
                            "indexes": self.toolset.dd_config.indexes,
                        },
                        "page": {"limit": 1000},  # Get all spans for the trace
                        "sort": "timestamp",
                    },
                }
            }

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            # Handle tuple response from POST requests
            if isinstance(response, tuple):
                spans, _ = response
            elif response:
                spans = response.get("data", [])
            else:
                spans = []

            # Format the trace hierarchy using the formatter
            formatted_output = format_trace_hierarchy(trace_id, spans)
            if not formatted_output:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params=params,
                    data=f"No trace found for trace_id: {trace_id}",
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=formatted_output,
                params=params,
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


class FetchDatadogSpansByFilter(BaseDatadogTracesTool):
    """Tool to search for spans with specific filters."""

    def __init__(self, toolset: "DatadogTracesToolset"):
        super().__init__(
            name="fetch_datadog_spans",
            description="[datadog/traces toolset] Search for spans in Datadog with detailed filters",
            parameters={
                "query": ToolParameter(
                    description="Datadog search query (e.g., 'service:web-app @http.status_code:500')",
                    type="string",
                    required=False,
                ),
                "service": ToolParameter(
                    description="Filter by service name",
                    type="string",
                    required=False,
                ),
                "operation": ToolParameter(
                    description="Filter by operation name",
                    type="string",
                    required=False,
                ),
                "resource": ToolParameter(
                    description="Filter by resource name",
                    type="string",
                    required=False,
                ),
                "tags": ToolParameter(
                    description="Filter by tags (e.g., {'env': 'production', 'version': '1.2.3'})",
                    type="object",
                    required=False,
                ),
                "start_datetime": ToolParameter(
                    description="Start time in RFC3339 format or relative time in seconds (negative for past)",
                    type="string",
                    required=False,
                ),
                "end_datetime": ToolParameter(
                    description="End time in RFC3339 format or relative time in seconds (negative for past)",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of spans to return",
                    type="integer",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        if "query" in params:
            return f"{toolset_name_for_one_liner(self.toolset.name)}: Search Spans ({params['query']})"

        filters = []
        if "service" in params:
            filters.append(f"service={params['service']}")
        if "operation" in params:
            filters.append(f"operation={params['operation']}")

        filter_str = ", ".join(filters) if filters else "all"
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Search Spans ({filter_str})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool to search spans."""
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

            # Build search query
            query_parts = []

            # If a custom query is provided, use it as the base
            if params.get("query"):
                query_parts.append(params["query"])

            # Add additional filters
            if params.get("service"):
                query_parts.append(f"service:{params['service']}")

            if params.get("operation"):
                query_parts.append(f"operation_name:{params['operation']}")

            if params.get("resource"):
                query_parts.append(f"resource_name:{params['resource']}")

            # Add tag filters
            if params.get("tags"):
                tags = params["tags"]
                if isinstance(tags, dict):
                    for key, value in tags.items():
                        query_parts.append(f"@{key}:{value}")

            query = " ".join(query_parts) if query_parts else "*"

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
                            "limit": params.get("limit", 100),
                        },
                        "sort": "-timestamp",
                    },
                }
            }

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            # Handle tuple response from POST requests
            if isinstance(response, tuple):
                spans, _ = response
            elif response:
                spans = response.get("data", [])
            else:
                spans = []

            # Format the spans search results using the formatter
            formatted_output = format_spans_search(spans)
            if not formatted_output:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    params=params,
                    data="No matching spans found.",
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=formatted_output,
                params=params,
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
