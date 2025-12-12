"""General-purpose Datadog API toolset for read-only operations."""

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

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
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.datadog.datadog_api import (
    DatadogBaseConfig,
    DataDogRequestError,
    execute_datadog_http_request,
    get_headers,
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
    preprocess_time_fields,
    enhance_error_message,
    fetch_openapi_spec,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

# Maximum response size in bytes (10MB)
MAX_RESPONSE_SIZE = 10 * 1024 * 1024

# Whitelisted API endpoint patterns with optional hints
# Format: (pattern, hint) - hint is empty string if no special instructions
WHITELISTED_ENDPOINTS = [
    # Monitors
    (r"^/api/v\d+/monitor(/search)?$", ""),
    (r"^/api/v\d+/monitor/\d+(/downtimes)?$", ""),
    (r"^/api/v\d+/monitor/groups/search$", ""),
    # Dashboards
    (r"^/api/v\d+/dashboard(/lists)?$", ""),
    (r"^/api/v\d+/dashboard/[^/]+$", ""),
    (r"^/api/v\d+/dashboard/public/[^/]+$", ""),
    # SLOs
    (r"^/api/v\d+/slo(/search)?$", ""),
    (r"^/api/v\d+/slo/[^/]+(/history)?$", ""),
    (r"^/api/v\d+/slo/[^/]+/corrections$", ""),
    # Events
    (
        r"^/api/v\d+/events$",
        "Use time range parameters 'start' and 'end' as Unix timestamps",
    ),
    (r"^/api/v\d+/events/\d+$", ""),
    # Incidents
    (r"^/api/v\d+/incidents(/search)?$", ""),
    (r"^/api/v\d+/incidents/[^/]+$", ""),
    (r"^/api/v\d+/incidents/[^/]+/attachments$", ""),
    (r"^/api/v\d+/incidents/[^/]+/connected_integrations$", ""),
    (r"^/api/v\d+/incidents/[^/]+/relationships$", ""),
    (r"^/api/v\d+/incidents/[^/]+/timeline$", ""),
    # Synthetics
    (r"^/api/v\d+/synthetics/tests(/search)?$", ""),
    (r"^/api/v\d+/synthetics/tests/[^/]+$", ""),
    (r"^/api/v\d+/synthetics/tests/[^/]+/results$", ""),
    (r"^/api/v\d+/synthetics/tests/browser/[^/]+/results$", ""),
    (r"^/api/v\d+/synthetics/tests/api/[^/]+/results$", ""),
    (r"^/api/v\d+/synthetics/locations$", ""),
    # Security
    (r"^/api/v\d+/security_monitoring/rules(/search)?$", ""),
    (r"^/api/v\d+/security_monitoring/rules/[^/]+$", ""),
    (r"^/api/v\d+/security_monitoring/signals(/search)?$", ""),
    (r"^/api/v\d+/security_monitoring/signals/[^/]+$", ""),
    # Services
    (r"^/api/v\d+/services$", ""),
    (r"^/api/v\d+/services/[^/]+$", ""),
    (r"^/api/v\d+/services/[^/]+/dependencies$", ""),
    (r"^/api/v\d+/service_dependencies$", ""),
    # Hosts
    (r"^/api/v\d+/hosts$", ""),
    (r"^/api/v\d+/hosts/totals$", ""),
    (r"^/api/v\d+/hosts/[^/]+$", ""),
    # Usage
    (r"^/api/v\d+/usage/[^/]+$", ""),
    (r"^/api/v\d+/usage/summary$", ""),
    (r"^/api/v\d+/usage/billable-summary$", ""),
    (r"^/api/v\d+/usage/cost_by_org$", ""),
    (r"^/api/v\d+/usage/estimated_cost$", ""),
    # Processes
    (r"^/api/v\d+/processes$", ""),
    # Tags
    (r"^/api/v\d+/tags/hosts(/[^/]+)?$", ""),
    # Notebooks
    (r"^/api/v\d+/notebooks$", ""),
    (r"^/api/v\d+/notebooks/\d+$", ""),
    # Organization
    (r"^/api/v\d+/org$", ""),
    (r"^/api/v\d+/org/[^/]+$", ""),
    # Users
    (r"^/api/v\d+/users$", ""),
    (r"^/api/v\d+/users/[^/]+$", ""),
    # Teams
    (r"^/api/v\d+/teams$", ""),
    (r"^/api/v\d+/teams/[^/]+$", ""),
    # Logs
    (
        r"^/api/v1/logs/config/indexes$",
        "When available, prefer using fetch_pod_logs tool from datadog/logs toolset instead of calling this API directly with the datadog/general toolset",
    ),
    (
        r"^/api/v2/logs/events$",
        "When available, prefer using fetch_pod_logs tool from datadog/logs toolset instead of calling this API directly with the datadog/general toolset. Use RFC3339 timestamps (e.g., '2024-01-01T00:00:00Z')",
    ),
    (
        r"^/api/v2/logs/events/search$",
        'When available, prefer using fetch_pod_logs tool from datadog/logs toolset instead of calling this API directly with the datadog/general toolset. RFC3339 time format. Example: {"filter": {"from": "2024-01-01T00:00:00Z", "to": "2024-01-02T00:00:00Z", "query": "*"}}',
    ),
    (
        r"^/api/v2/logs/analytics/aggregate$",
        "When available, prefer using fetch_pod_logs tool from datadog/logs toolset instead of calling this API directly with the datadog/general toolset. Do not include 'sort' parameter",
    ),
    # Metrics
    (
        r"^/api/v\d+/metrics$",
        "When available, prefer using query_datadog_metrics tool from datadog/metrics toolset instead of calling this API directly with the datadog/general toolset",
    ),
    (
        r"^/api/v\d+/metrics/[^/]+$",
        "When available, prefer using get_datadog_metric_metadata tool from datadog/metrics toolset instead of calling this API directly with the datadog/general toolset",
    ),
    (
        r"^/api/v\d+/query$",
        "When available, prefer using query_datadog_metrics tool from datadog/metrics toolset instead of calling this API directly with the datadog/general toolset. Use 'from' and 'to' as Unix timestamps",
    ),
    (
        r"^/api/v\d+/search/query$",
        "When available, prefer using query_datadog_metrics tool from datadog/metrics toolset instead of calling this API directly with the datadog/general toolset",
    ),
]

# Blacklisted path segments that indicate write operations
BLACKLISTED_SEGMENTS = [
    "/create",
    "/update",
    "/delete",
    "/patch",
    "/remove",
    "/add",
    "/revoke",
    "/cancel",
    "/mute",
    "/unmute",
    "/enable",
    "/disable",
    "/archive",
    "/unarchive",
    "/assign",
    "/unassign",
    "/invite",
    "/bulk",
    "/import",
    "/export",
    "/trigger",
    "/validate",
    "/execute",
    "/run",
    "/start",
    "/stop",
    "/restart",
]

# POST endpoints that are allowed (search/query operations only)
WHITELISTED_POST_ENDPOINTS = [
    r"^/api/v\d+/monitor/search$",
    r"^/api/v\d+/dashboard/lists$",
    r"^/api/v\d+/slo/search$",
    r"^/api/v\d+/events/search$",
    r"^/api/v\d+/incidents/search$",
    r"^/api/v\d+/synthetics/tests/search$",
    r"^/api/v\d+/security_monitoring/rules/search$",
    r"^/api/v\d+/security_monitoring/signals/search$",
    r"^/api/v\d+/logs/events/search$",
    r"^/api/v2/logs/events/search$",
    r"^/api/v2/logs/analytics/aggregate$",
    r"^/api/v\d+/spans/events/search$",
    r"^/api/v\d+/rum/events/search$",
    r"^/api/v\d+/audit/events/search$",
    r"^/api/v\d+/query$",
    r"^/api/v\d+/search/query$",
]


class DatadogGeneralConfig(DatadogBaseConfig):
    """Configuration for general-purpose Datadog toolset."""

    max_response_size: int = MAX_RESPONSE_SIZE
    allow_custom_endpoints: bool = (
        False  # If True, allows endpoints not in whitelist (still filtered for safety)
    )


class DatadogGeneralToolset(Toolset):
    """General-purpose Datadog API toolset for read-only operations not covered by specialized toolsets."""

    dd_config: Optional[DatadogGeneralConfig] = None
    openapi_spec: Optional[Dict[str, Any]] = None

    def __init__(self):
        super().__init__(
            name="datadog/general",
            description="General-purpose Datadog API access for read-only operations including monitors, dashboards, SLOs, incidents, synthetics, logs, metrics, and more. Note: For logs and metrics, prefer using the specialized datadog/logs and datadog/metrics toolsets when available as they provide optimized functionality",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            icon_url="https://imgix.datadoghq.com//img/about/presskit/DDlogo.jpg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                DatadogAPIGet(toolset=self),
                DatadogAPIPostSearch(toolset=self),
                ListDatadogAPIResources(toolset=self),
            ],
            tags=[ToolsetTag.CORE],
        )
        template_file_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "datadog_general_instructions.jinja2"
            )
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        """Check prerequisites with configuration."""
        if not config:
            return (
                False,
                "Missing config for dd_api_key, dd_app_key, or site_api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            )

        try:
            dd_config = DatadogGeneralConfig(**config)
            self.dd_config = dd_config

            # Fetch OpenAPI spec on startup for better error messages and documentation
            logging.debug("Fetching Datadog OpenAPI specification...")
            self.openapi_spec = fetch_openapi_spec(version="both")
            if self.openapi_spec:
                logging.info(
                    f"Successfully loaded OpenAPI spec with {len(self.openapi_spec.get('paths', {}))} endpoints"
                )
            else:
                logging.warning(
                    "Could not fetch OpenAPI spec; enhanced error messages will be limited"
                )

            success, error_msg = self._perform_healthcheck(dd_config)
            return success, error_msg
        except Exception as e:
            logging.exception("Failed to set up Datadog general toolset")
            return False, f"Failed to parse Datadog configuration: {str(e)}"

    def _perform_healthcheck(self, dd_config: DatadogGeneralConfig) -> Tuple[bool, str]:
        """Perform health check on Datadog API."""
        try:
            logging.info("Performing Datadog general API configuration healthcheck...")
            base_url = str(dd_config.site_api_url).rstrip("/")
            url = f"{base_url}/api/v1/validate"
            headers = get_headers(dd_config)

            data = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params={},
                timeout=dd_config.request_timeout,
                method="GET",
            )

            if data.get("valid", False):
                logging.debug("Datadog general API healthcheck completed successfully")
                return True, ""
            else:
                error_msg = "Datadog API key validation failed"
                logging.error(f"Datadog general API healthcheck failed: {error_msg}")
                return False, f"Datadog general API healthcheck failed: {error_msg}"

        except Exception as e:
            logging.exception("Failed during Datadog general API healthcheck")
            return False, f"Healthcheck failed with exception: {str(e)}"

    def get_example_config(self) -> Dict[str, Any]:
        """Get example configuration for this toolset."""
        return {
            "dd_api_key": "your-datadog-api-key",
            "dd_app_key": "your-datadog-application-key",
            "site_api_url": "https://api.datadoghq.com",
            "max_response_size": MAX_RESPONSE_SIZE,
            "allow_custom_endpoints": False,
        }


def is_endpoint_allowed(
    endpoint: str, method: str = "GET", allow_custom: bool = False
) -> Tuple[bool, str]:
    """
    Check if an endpoint is allowed based on whitelist and safety rules.

    Returns:
        Tuple of (is_allowed, error_message)
    """
    # Parse the endpoint
    parsed = urlparse(endpoint)
    path = parsed.path

    # Check for blacklisted segments
    path_lower = path.lower()
    for segment in BLACKLISTED_SEGMENTS:
        if segment in path_lower:
            return False, f"Endpoint contains blacklisted operation '{segment}'"

    # Check method-specific whitelists
    if method == "POST":
        for pattern in WHITELISTED_POST_ENDPOINTS:
            if re.match(pattern, path):
                return True, ""
        return False, f"POST method not allowed for endpoint: {path}"

    elif method == "GET":
        for pattern, _ in WHITELISTED_ENDPOINTS:
            if re.match(pattern, path):
                return True, ""

        # If custom endpoints are allowed and no blacklisted segments found
        if allow_custom:
            return True, ""

        return False, f"Endpoint not in whitelist: {path}"

    else:
        return False, f"HTTP method {method} not allowed for {path}"


def get_endpoint_hint(endpoint: str) -> str:
    """
    Get hint for an endpoint if available.

    Returns:
        Hint string or empty string if no hint
    """
    parsed = urlparse(endpoint)
    path = parsed.path

    for pattern, hint in WHITELISTED_ENDPOINTS:
        if re.match(pattern, path):
            return hint

    return ""


class BaseDatadogGeneralTool(Tool):
    """Base class for general Datadog API tools."""

    toolset: "DatadogGeneralToolset"


class DatadogAPIGet(BaseDatadogGeneralTool):
    """Tool for making GET requests to Datadog API."""

    def __init__(self, toolset: "DatadogGeneralToolset"):
        super().__init__(
            name="datadog_api_get",
            description="[datadog/general toolset] Make a GET request to a Datadog API endpoint for read-only operations",
            parameters={
                "endpoint": ToolParameter(
                    description="The API endpoint path (e.g., '/api/v1/monitors', '/api/v2/events')",
                    type="string",
                    required=True,
                ),
                "query_params": ToolParameter(
                    description="""Query parameters as a dictionary.
                    Time format requirements:
                    - v1 API: Unix timestamps in seconds (e.g., {'start': 1704067200, 'end': 1704153600})
                    - v2 API: RFC3339 format (e.g., {'from': '2024-01-01T00:00:00Z', 'to': '2024-01-02T00:00:00Z'})
                    - Relative times like '-24h', 'now', '-7d' will be auto-converted to proper format

                    Example for events: {'start': 1704067200, 'end': 1704153600}
                    Example for monitors: {'name': 'my-monitor', 'tags': 'env:prod'}""",
                    type="object",
                    required=False,
                ),
                "description": ToolParameter(
                    description="Brief description of what this API call is retrieving",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        description = params.get("description", "API call")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: {description}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the GET request."""
        logging.info("=" * 60)
        logging.info("DatadogAPIGet Tool Invocation:")
        logging.info(f"  Description: {params.get('description', 'No description')}")
        logging.info(f"  Endpoint: {params.get('endpoint', '')}")
        logging.info(
            f"  Query Params: {json.dumps(params.get('query_params', {}), indent=2)}"
        )
        logging.info("=" * 60)

        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        endpoint = params.get("endpoint", "")
        query_params = params.get("query_params", {})

        # Validate endpoint
        is_allowed, error_msg = is_endpoint_allowed(
            endpoint,
            method="GET",
            allow_custom=self.toolset.dd_config.allow_custom_endpoints,
        )
        if not is_allowed:
            logging.error(f"Endpoint validation failed: {error_msg}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Endpoint validation failed: {error_msg}",
                params=params,
            )

        url = None
        try:
            # Build full URL (ensure no double slashes)
            base_url = str(self.toolset.dd_config.site_api_url).rstrip("/")
            endpoint = endpoint.lstrip("/")
            url = f"{base_url}/{endpoint}"
            headers = get_headers(self.toolset.dd_config)

            logging.info(f"Full API URL: {url}")

            # Preprocess time fields if any
            processed_params = preprocess_time_fields(query_params, endpoint)

            # Execute request
            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=processed_params,
                timeout=self.toolset.dd_config.request_timeout,
                method="GET",
            )

            # Check response size
            response_str = json.dumps(response, indent=2)
            if (
                len(response_str.encode("utf-8"))
                > self.toolset.dd_config.max_response_size
            ):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Response too large (>{self.toolset.dd_config.max_response_size} bytes)",
                    params=params,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_str,
                params=params,
            )

        except DataDogRequestError as e:
            logging.exception(e, exc_info=True)

            if e.status_code == 429:
                error_msg = f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
            elif e.status_code == 403:
                error_msg = (
                    f"Permission denied. Check API key permissions. Error: {str(e)}"
                )
            elif e.status_code == 404:
                error_msg = f"Endpoint not found: {endpoint}"
            elif e.status_code == 400:
                # Use enhanced error message for 400 errors
                error_msg = enhance_error_message(
                    e, endpoint, "GET", str(self.toolset.dd_config.site_api_url)
                )
            else:
                error_msg = f"API error {e.status_code}: {str(e)}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
                invocation=json.dumps({"url": url, "params": query_params})
                if url
                else None,
            )

        except Exception as e:
            logging.exception(f"Failed to query Datadog API: {params}", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
            )


class DatadogAPIPostSearch(BaseDatadogGeneralTool):
    """Tool for making POST requests to Datadog search/query endpoints."""

    def __init__(self, toolset: "DatadogGeneralToolset"):
        super().__init__(
            name="datadog_api_post_search",
            description="[datadog/general toolset] Make a POST request to Datadog search/query endpoints for complex filtering",
            parameters={
                "endpoint": ToolParameter(
                    description="The search API endpoint (e.g., '/api/v2/monitor/search', '/api/v2/events/search')",
                    type="string",
                    required=True,
                ),
                "body": ToolParameter(
                    description="""Request body for the search/filter operation.
                    Time format requirements:
                    - v1 API: Unix timestamps (e.g., 1704067200)
                    - v2 API: RFC3339 format (e.g., '2024-01-01T00:00:00Z')
                    - Relative times like '-24h', 'now', '-7d' will be auto-converted

                    Example for logs search:
                    {
                      "filter": {
                        "from": "2024-01-01T00:00:00Z",
                        "to": "2024-01-02T00:00:00Z",
                        "query": "*"
                      },
                      "sort": "-timestamp",
                      "page": {"limit": 50}
                    }

                    Example for monitor search:
                    {
                      "query": "env:production",
                      "page": 0,
                      "per_page": 20
                    }""",
                    type="object",
                    required=True,
                ),
                "description": ToolParameter(
                    description="Brief description of what this search is looking for",
                    type="string",
                    required=True,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        description = params.get("description", "Search")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: {description}"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the POST search request."""
        logging.info("=" * 60)
        logging.info("DatadogAPIPostSearch Tool Invocation:")
        logging.info(f"  Description: {params.get('description', 'No description')}")
        logging.info(f"  Endpoint: {params.get('endpoint', '')}")
        logging.info(f"  Body: {json.dumps(params.get('body', {}), indent=2)}")
        logging.info("=" * 60)

        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        endpoint = params.get("endpoint", "")
        body = params.get("body", {})

        # Validate endpoint
        is_allowed, error_msg = is_endpoint_allowed(
            endpoint,
            method="POST",
            allow_custom=self.toolset.dd_config.allow_custom_endpoints,
        )
        if not is_allowed:
            logging.error(f"Endpoint validation failed: {error_msg}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Endpoint validation failed: {error_msg}",
                params=params,
            )

        url = None
        try:
            # Build full URL (ensure no double slashes)
            base_url = str(self.toolset.dd_config.site_api_url).rstrip("/")
            endpoint = endpoint.lstrip("/")
            url = f"{base_url}/{endpoint}"
            headers = get_headers(self.toolset.dd_config)

            logging.info(f"Full API URL: {url}")

            # Preprocess time fields if any
            processed_body = preprocess_time_fields(body, endpoint)

            # Execute request
            response = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params=processed_body,
                timeout=self.toolset.dd_config.request_timeout,
                method="POST",
            )

            # Check response size
            response_str = json.dumps(response, indent=2)
            if (
                len(response_str.encode("utf-8"))
                > self.toolset.dd_config.max_response_size
            ):
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Response too large (>{self.toolset.dd_config.max_response_size} bytes)",
                    params=params,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_str,
                params=params,
            )

        except DataDogRequestError as e:
            logging.exception(e, exc_info=True)

            if e.status_code == 429:
                error_msg = f"Datadog API rate limit exceeded. Failed after {MAX_RETRY_COUNT_ON_RATE_LIMIT} retry attempts."
            elif e.status_code == 403:
                error_msg = (
                    f"Permission denied. Check API key permissions. Error: {str(e)}"
                )
            elif e.status_code == 404:
                error_msg = f"Endpoint not found: {endpoint}"
            elif e.status_code == 400:
                # Use enhanced error message for 400 errors
                error_msg = enhance_error_message(
                    e, endpoint, "POST", str(self.toolset.dd_config.site_api_url)
                )
            else:
                error_msg = f"API error {e.status_code}: {str(e)}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params,
                invocation=json.dumps({"url": url, "body": body}) if url else None,
            )

        except Exception as e:
            logging.exception(f"Failed to query Datadog API: {params}", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
            )


class ListDatadogAPIResources(BaseDatadogGeneralTool):
    """Tool for listing available Datadog API resources and endpoints."""

    def __init__(self, toolset: "DatadogGeneralToolset"):
        super().__init__(
            name="list_datadog_api_resources",
            description="[datadog/general toolset] List available Datadog API resources and endpoints that can be accessed",
            parameters={
                "search_regex": ToolParameter(
                    description="Optional regex pattern to filter endpoints (e.g., 'monitor', 'logs|metrics', 'security.*signals', 'v2/.*search$'). If not provided, shows all endpoints.",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Get a one-liner description of the tool invocation."""
        search = params.get("search_regex", "all")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: List API Resources (search: {search})"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """List available API resources."""
        search_regex = params.get("search_regex", "")

        logging.info("=" * 60)
        logging.info("ListDatadogAPIResources Tool Invocation:")
        logging.info(f"  Search regex: {search_regex or 'None (showing all)'}")
        logging.info(f"  OpenAPI Spec Loaded: {self.toolset.openapi_spec is not None}")
        logging.info("=" * 60)

        # Filter endpoints based on regex search
        matching_endpoints = []

        if search_regex:
            try:
                search_pattern = re.compile(search_regex, re.IGNORECASE)
            except re.error as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid regex pattern: {e}",
                    params=params,
                )
        else:
            search_pattern = None

        # Build list of matching endpoints
        for pattern, hint in WHITELISTED_ENDPOINTS:
            # Create a readable endpoint example from the pattern
            example_endpoint = pattern.replace(r"^/api/v\d+", "/api/v1")
            example_endpoint = example_endpoint.replace(r"(/search)?$", "")
            example_endpoint = example_endpoint.replace(r"(/[^/]+)?$", "/{id}")
            example_endpoint = example_endpoint.replace(r"/[^/]+$", "/{id}")
            example_endpoint = example_endpoint.replace(r"/\d+$", "/{id}")
            example_endpoint = example_endpoint.replace("$", "")
            example_endpoint = example_endpoint.replace("^", "")

            # Apply search filter if provided
            if search_pattern and not search_pattern.search(example_endpoint):
                continue

            # Determine HTTP methods
            if "search" in pattern or "query" in pattern or "aggregate" in pattern:
                methods = "POST"
            elif "/search)?$" in pattern:
                methods = "GET/POST"
            else:
                methods = "GET"

            endpoint_info = {
                "endpoint": example_endpoint,
                "methods": methods,
                "hint": hint,
                "pattern": pattern,
            }
            matching_endpoints.append(endpoint_info)

        if not matching_endpoints:
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=f"No endpoints found matching regex: {search_regex}",
                params=params,
            )

        # Format output
        output = ["Available Datadog API Endpoints", "=" * 40]

        if search_regex:
            output.append(f"Filter: {search_regex}")
        output.append(f"Found: {len(matching_endpoints)} endpoints")
        output.append("")

        # List endpoints with spec info if available
        for info in matching_endpoints:
            line = f"{info['methods']:8} {info['endpoint']}"
            if info["hint"]:
                line += f"\n         {info['hint']}"

            # Add OpenAPI spec info for this specific endpoint if available
            if self.toolset.openapi_spec and "paths" in self.toolset.openapi_spec:
                # Try to find matching path in OpenAPI spec
                spec_path = None
                for path in self.toolset.openapi_spec["paths"].keys():
                    if re.match(info["pattern"], path):
                        spec_path = path
                        break

                if spec_path and spec_path in self.toolset.openapi_spec["paths"]:
                    path_spec = self.toolset.openapi_spec["paths"][spec_path]
                    # Add actual OpenAPI schema for the endpoint
                    for method in ["get", "post", "put", "delete"]:
                        if method in path_spec:
                            method_spec = path_spec[method]
                            line += f"\n\n         OpenAPI Schema ({method.upper()}):"

                            # Add summary if available
                            if "summary" in method_spec:
                                line += f"\n         Summary: {method_spec['summary']}"

                            # Add parameters if available
                            if "parameters" in method_spec:
                                line += "\n         Parameters:"
                                for param in method_spec["parameters"]:
                                    param_info = f"\n           - {param.get('name', 'unknown')} ({param.get('in', 'unknown')})"
                                    if param.get("required", False):
                                        param_info += " [required]"
                                    if "description" in param:
                                        param_info += f": {param['description'][:100]}"
                                    line += param_info

                            # Add request body schema if available
                            if "requestBody" in method_spec:
                                line += "\n         Request Body:"
                                if "content" in method_spec["requestBody"]:
                                    for content_type, content_spec in method_spec[
                                        "requestBody"
                                    ]["content"].items():
                                        if "schema" in content_spec:
                                            # Show a compact version of the schema
                                            schema_str = json.dumps(
                                                content_spec["schema"], indent=10
                                            )[:500]
                                            if (
                                                len(json.dumps(content_spec["schema"]))
                                                > 500
                                            ):
                                                schema_str += "..."
                                            line += f"\n           Content-Type: {content_type}"
                                            line += f"\n           Schema: {schema_str}"

                            # Add response schema sample if available
                            if "responses" in method_spec:
                                if "200" in method_spec["responses"]:
                                    line += "\n         Response (200):"
                                    resp = method_spec["responses"]["200"]
                                    if "description" in resp:
                                        line += f"\n           {resp['description']}"
                            break

            output.append(line)

        output.append("")
        output.append(
            "Note: All endpoints are read-only. Use the appropriate tool with the endpoint path."
        )
        output.append("Example: datadog_api_get with endpoint='/api/v1/monitors'")
        output.append("")
        output.append("Search examples:")
        output.append("  • 'monitor' - find all monitor endpoints")
        output.append("  • 'logs|metrics' - find logs OR metrics endpoints")
        output.append("  • 'v2.*search$' - find all v2 search endpoints")
        output.append("  • 'security.*signals' - find security signals endpoints")

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="\n".join(output),
            params=params,
        )
