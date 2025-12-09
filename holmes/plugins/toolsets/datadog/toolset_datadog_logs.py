import os
from enum import Enum
import json
import logging
from typing import Any, Dict, Tuple
from urllib.parse import urlencode
from holmes.core.tools import (
    CallablePrerequisite,
    ToolsetTag,
    ToolInvokeContext,
    Tool,
    ToolParameter,
)
from pydantic import BaseModel, Field
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.datadog.datadog_api import (
    DatadogBaseConfig,
    DataDogRequestError,
    execute_datadog_http_request,
    execute_paginated_datadog_http_request,
    get_headers,
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
    preprocess_time_fields,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_TIME_SPAN_SECONDS,
    DEFAULT_LOG_LIMIT,
    Toolset,
    FetchPodLogsParams,
)

from holmes.plugins.toolsets.consts import STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_int,
    toolset_name_for_one_liner,
    standard_start_datetime_tool_param_description,
)

from holmes.plugins.toolsets.utils import process_timestamps_to_rfc3339


class DataDogLabelsMapping(BaseModel):
    pod: str = "pod_name"
    namespace: str = "kube_namespace"


class DataDogStorageTier(str, Enum):
    INDEXES = "indexes"
    ONLINE_ARCHIVES = "online-archives"
    FLEX = "flex"


DEFAULT_STORAGE_TIERS = [DataDogStorageTier.INDEXES]


class DatadogLogsConfig(DatadogBaseConfig):
    indexes: list[str] = ["*"]
    # Ordered list of storage tiers. Works as fallback. Subsequent tiers are queried only if the previous tier yielded no result
    storage_tiers: list[DataDogStorageTier] = Field(
        default=DEFAULT_STORAGE_TIERS, min_length=1
    )
    labels: DataDogLabelsMapping = DataDogLabelsMapping()
    page_size: int = 300
    default_limit: int = DEFAULT_LOG_LIMIT


def calculate_page_size(
    params: FetchPodLogsParams, dd_config: DatadogLogsConfig, logs: list
) -> int:
    logs_count = len(logs)

    max_logs_count = dd_config.default_limit
    if params.limit:
        max_logs_count = params.limit

    return min(dd_config.page_size, max(0, max_logs_count - logs_count))


def fetch_paginated_logs(
    params: FetchPodLogsParams,
    dd_config: DatadogLogsConfig,
    storage_tier: DataDogStorageTier,
) -> list[dict]:
    limit = params.limit or dd_config.default_limit

    (from_time, to_time) = process_timestamps_to_rfc3339(
        start_timestamp=params.start_time,
        end_timestamp=params.end_time,
        default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
    )

    url = f"{dd_config.site_api_url}/api/v2/logs/events/search"
    headers = get_headers(dd_config)

    query = f"{dd_config.labels.namespace}:{params.namespace}"
    query += f" {dd_config.labels.pod}:{params.pod_name}"
    if params.filter:
        filter = params.filter.replace('"', '\\"')
        query += f' "{filter}"'

    payload: Dict[str, Any] = {
        "filter": {
            "from": from_time,
            "to": to_time,
            "query": query,
            "indexes": dd_config.indexes,
            "storage_tier": storage_tier.value,
        },
        "sort": "-timestamp",
        "page": {"limit": calculate_page_size(params, dd_config, [])},
    }

    # Preprocess time fields to ensure correct format
    processed_payload = preprocess_time_fields(payload, "/api/v2/logs/events/search")

    logs, cursor = execute_paginated_datadog_http_request(
        url=url,
        headers=headers,
        payload_or_params=processed_payload,
        timeout=dd_config.request_timeout,
    )

    while cursor and len(logs) < limit:
        processed_payload["page"]["cursor"] = cursor
        processed_payload["page"]["limit"] = calculate_page_size(
            params, dd_config, logs
        )
        new_logs, cursor = execute_paginated_datadog_http_request(
            url=url,
            headers=headers,
            payload_or_params=processed_payload,
            timeout=dd_config.request_timeout,
        )
        logs += new_logs

    # logs are fetched descending order. Unified logging API follows the pattern of kubectl logs where oldest logs are first
    logs.reverse()

    if len(logs) > limit:
        logs = logs[-limit:]
    return logs


def format_logs(raw_logs: list[dict]) -> str:
    logs = []

    for raw_log_item in raw_logs:
        # Extract timestamp - Datadog returns it in ISO format
        timestamp = raw_log_item.get("attributes", {}).get("timestamp", "")
        if not timestamp:
            # Fallback to @timestamp if timestamp is not in attributes
            timestamp = raw_log_item.get("attributes", {}).get("@timestamp", "")

        # Extract message
        message = raw_log_item.get("attributes", {}).get(
            "message", json.dumps(raw_log_item)
        )

        # Format as: [timestamp] message
        if timestamp:
            logs.append(f"[{timestamp}] {message}")
        else:
            logs.append(message)

    return "\n".join(logs)


def generate_datadog_logs_url(
    dd_config: DatadogLogsConfig,
    params: FetchPodLogsParams,
    storage_tier: DataDogStorageTier,
) -> str:
    """Generate a Datadog web UI URL for the logs query."""
    from holmes.plugins.toolsets.utils import process_timestamps_to_int
    from holmes.plugins.toolsets.datadog.datadog_api import convert_api_url_to_app_url

    # Convert API URL to app URL using the shared helper
    base_url = convert_api_url_to_app_url(dd_config.site_api_url)

    # Build the query string
    query = f"{dd_config.labels.namespace}:{params.namespace}"
    query += f" {dd_config.labels.pod}:{params.pod_name}"
    if params.filter:
        filter = params.filter.replace('"', '\\"')
        query += f' "{filter}"'

    # Process timestamps - get Unix timestamps in seconds
    (from_time_seconds, to_time_seconds) = process_timestamps_to_int(
        start=params.start_time,
        end=params.end_time,
        default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
    )

    # Convert to milliseconds for Datadog web UI
    from_time_ms = from_time_seconds * 1000
    to_time_ms = to_time_seconds * 1000

    # Build URL parameters matching Datadog's web UI format
    url_params = {
        "query": query,
        "from_ts": str(from_time_ms),
        "to_ts": str(to_time_ms),
        "live": "true",
        "storage": storage_tier.value,
    }

    # Add indexes if not default
    if dd_config.indexes != ["*"]:
        url_params["index"] = ",".join(dd_config.indexes)

    # Construct the full URL
    return f"{base_url}/logs?{urlencode(url_params)}"


class DatadogLogsToolset(Toolset):
    """Toolset for working with Datadog logs data."""

    dd_config: DatadogLogsConfig

    def __init__(self):
        super().__init__(
            name="datadog/logs",
            description="Toolset for fetching logs from Datadog, including historical data for pods no longer in the cluster",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            icon_url="https://imgix.datadoghq.com//img/about/presskit/DDlogo.jpg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],  # Initialize with empty tools first
            tags=[ToolsetTag.CORE],
        )
        # Now that parent is initialized and self.name exists, create the tool
        self.tools = [GetLogs(self)]
        self._reload_instructions()

    def _perform_healthcheck(self) -> Tuple[bool, str]:
        """Perform health check on Datadog logs API."""
        try:
            logging.info("Performing Datadog logs configuration healthcheck...")
            headers = get_headers(self.dd_config)
            payload = {
                "filter": {
                    "from": "now-1m",
                    "to": "now",
                    "query": "*",
                    "indexes": self.dd_config.indexes,
                },
                "page": {"limit": 1},
            }

            search_url = f"{self.dd_config.site_api_url}/api/v2/logs/events/search"
            execute_datadog_http_request(
                url=search_url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.dd_config.request_timeout,
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

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return (
                False,
                "Missing config for dd_api_key, dd_app_key, or site_api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            )

        try:
            dd_config = DatadogLogsConfig(**config)
            self.dd_config = dd_config

            # Perform healthcheck
            success, error_msg = self._perform_healthcheck()
            return success, error_msg

        except Exception as e:
            logging.exception("Failed to set up Datadog toolset")
            return (False, f"Failed to parse Datadog configuration: {str(e)}")

    def get_example_config(self) -> Dict[str, Any]:
        return {
            "dd_api_key": "your-datadog-api-key",
            "dd_app_key": "your-datadog-application-key",
            "site_api_url": "https://api.datadoghq.com",
        }

    def _reload_instructions(self):
        """Load Datadog logs specific troubleshooting instructions."""
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "datadog_logs_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")


class GetLogs(Tool):
    toolset: "DatadogLogsToolset"
    """Tool to search for spans with specific filters."""

    def __init__(self, toolset: "DatadogLogsToolset"):
        toolset = toolset
        super().__init__(
            name="fetch_datadog_logs",
            description="Search for logs in Datadog using search query syntax"
            "Uses the DataDog api endpoint: POST /api/v2/logs/events/search with 'query' parameter. (e.g., 'service:web-app @http.status_code:500')",
            parameters={
                "query": ToolParameter(
                    description="The search query - following the logs search syntax. default: *",
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
                "cursor": ToolParameter(
                    description="The returned paging point to use to get the next results.",
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description=f"Maximum number of spans to return. default: {DEFAULT_LOG_LIMIT}",
                    type="integer",
                    required=False,
                ),
                "sort_desc": ToolParameter(
                    description="Get the results in descending order. default: true",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Search Logs ({params['query'] if 'query' in params else ''})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool to search logs."""
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

            query = params.get("query") if params.get("query") else "*"
            limit = params.get("limit") if params.get("limit") else DEFAULT_LOG_LIMIT
            if params.get("sort") is not None:
                sort = "-timestamp" if params.get("sort") else True
            else:
                sort = "-timestamp"

            url = f"{self.toolset.dd_config.site_api_url}/api/v2/logs/events/search"
            headers = get_headers(self.toolset.dd_config)

            payload = {
                "filter": {
                    "query": query,
                    "from": str(from_time_ms),
                    "to": str(to_time_ms),
                    "storage_tier": self.toolset.dd_config.storage_tiers[-1],
                    "indexes": self.toolset.dd_config.indexes,
                },
                "page": {
                    "limit": limit,
                },
                "sort": sort,
            }

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response,
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
