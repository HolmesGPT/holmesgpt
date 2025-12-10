import os
from enum import Enum
import json
import logging
from typing import Any, Dict, Tuple, Optional
from holmes.core.tools import (
    CallablePrerequisite,
    ToolsetTag,
    ToolInvokeContext,
    Tool,
    ToolParameter,
)
from pydantic import Field
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.datadog.datadog_api import (
    DatadogBaseConfig,
    DataDogRequestError,
    execute_datadog_http_request,
    get_headers,
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
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


class DatadogLogsToolset(Toolset):
    """Toolset for working with Datadog logs data."""

    dd_config: Optional[DatadogLogsConfig] = None

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
        self.tools = [GetLogs(toolset=self)]
        self._reload_instructions()

    def _perform_healthcheck(self) -> Tuple[bool, str]:
        """Perform health check on Datadog logs API."""
        if not self.dd_config:
            return False, "Datadog configuration not initialized"
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
    """Tool to search for logs with specific search query."""

    toolset: "DatadogLogsToolset"
    name: str = "fetch_datadog_logs"
    description: str = "Search for logs in Datadog using search query syntax"
    "Uses the DataDog api endpoint: POST /api/v2/logs/events/search with 'query' parameter. (e.g., 'service:web-app @http.status_code:500')"
    parameters: Dict[str, ToolParameter] = {
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
    }

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Search Logs ({params['query'] if 'query' in params else ''})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the tool to search logs."""
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
