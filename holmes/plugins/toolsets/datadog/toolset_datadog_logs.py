import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

from pydantic import AnyUrl

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    ToolsetTag,
)
from holmes.plugins.toolsets.consts import STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION
from holmes.plugins.toolsets.datadog.datadog_api import (
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
    DataDogRequestError,
    execute_datadog_http_request,
    get_headers,
)
from holmes.plugins.toolsets.datadog.datadog_models import (
    DatadogLogsConfig,
)
from holmes.plugins.toolsets.datadog.datadog_url_utils import generate_datadog_logs_url
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_LOG_LIMIT,
    DEFAULT_TIME_SPAN_SECONDS,
    Toolset,
)
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_int,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)


def format_logs(raw_logs: list[dict]) -> str:
    # Use similar structure to Datadog Log Explorer
    logs = []

    for raw_log_item in raw_logs:
        attrs = raw_log_item.get("attributes", {})

        timestamp = attrs.get("timestamp") or attrs.get("@timestamp", "")
        host = attrs.get("host", "")
        service = attrs.get("service", "")
        status = attrs.get("attributes", {}).get("status") or attrs.get("status", "")
        message = attrs.get("message", json.dumps(raw_log_item))
        tags = attrs.get("tags", [])

        pod_name_tag = next((t for t in tags if t.startswith("pod_")), "")

        log_line = f"{timestamp} {host} {pod_name_tag} {service} {status} {message}"
        logs.append(log_line)

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

            success, error_msg = self._perform_healthcheck()
            return success, error_msg

        except Exception as e:
            logging.exception("Failed to set up Datadog toolset")
            return (False, f"Failed to parse Datadog configuration: {str(e)}")

    def get_example_config(self) -> Dict[str, Any]:
        """Get example configuration for this toolset."""
        example_config = DatadogLogsConfig(
            dd_api_key="<your_datadog_api_key>",
            dd_app_key="<your_datadog_app_key>",
            site_api_url=AnyUrl("https://api.datadoghq.com"),
        )
        return example_config.model_dump(mode="json")

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
            description="The returned paging point to use to get the next results. IMPORTANT: Cursors are single-use and stateful - never reuse the same cursor value multiple times or parallelize cursor-based calls. Each response provides a new cursor for the subsequent request.",
            type="string",
            required=False,
        ),
        "limit": ToolParameter(
            description=f"Maximum number of log records to return. Defaults to {DEFAULT_LOG_LIMIT}. This value is user-configured and represents the maximum allowed limit.",
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

            config_limit = self.toolset.dd_config.default_limit
            limit = min(params.get("limit", config_limit), config_limit)
            params["limit"] = limit
            sort = "timestamp" if params.get("sort_desc", False) else "-timestamp"

            url = f"{self.toolset.dd_config.site_api_url}/api/v2/logs/events/search"
            headers = get_headers(self.toolset.dd_config)

            storage = self.toolset.dd_config.storage_tiers[-1]
            payload = {
                "filter": {
                    "query": params.get("query", "*"),
                    "from": str(from_time_ms),
                    "to": str(to_time_ms),
                    "storage_tier": storage,
                    "indexes": self.toolset.dd_config.indexes,
                },
                "page": {
                    "limit": limit,
                },
                "sort": sort,
            }

            if params.get("cursor"):
                payload["page"]["cursor"] = params["cursor"]

            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=payload,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            if self.toolset.dd_config.compact_logs and response.get("data"):
                response["data"] = format_logs(response["data"])

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response,
                params=params,
                url=generate_datadog_logs_url(self.toolset.dd_config, payload),
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
