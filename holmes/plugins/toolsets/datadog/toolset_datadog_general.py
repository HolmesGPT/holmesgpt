"""General-purpose Datadog API toolset for read-only operations."""

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from holmes.core.tools import (
    CallablePrerequisite,
    ClassVar,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
    Type,
)
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.datadog.datadog_api import (
    MAX_RETRY_COUNT_ON_RATE_LIMIT,
    DataDogRequestError,
    enhance_error_message,
    execute_datadog_http_request,
    fetch_openapi_spec,
    get_headers,
    preprocess_time_fields,
)
from holmes.plugins.toolsets.datadog.datadog_models import (
    DatadogGeneralConfig,
)
from holmes.plugins.toolsets.datadog.datadog_url_utils import (
    generate_datadog_general_url,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


@dataclass
class EndpointTemplate:
    """Represents a valid Datadog API endpoint template."""

    template: str  # e.g., "/api/v1/monitor/{monitor_id}"
    description: str  # Description for the LLM
    placeholder_name: Optional[str] = None  # e.g., "monitor_id", None if no placeholder


# GET endpoint templates - schema-constrained list of valid endpoints
# Each entry defines an explicit template with optional resource_id placeholder
GET_ENDPOINT_TEMPLATES: List[EndpointTemplate] = [
    # Monitors
    EndpointTemplate("/api/v1/monitor", "List all monitors"),
    EndpointTemplate(
        "/api/v1/monitor/{monitor_id}",
        "Get a specific monitor by ID",
        "monitor_id",
    ),
    EndpointTemplate("/api/v1/monitor/groups/search", "Search monitor groups"),
    # Dashboards
    EndpointTemplate("/api/v1/dashboard", "List all dashboards"),
    EndpointTemplate("/api/v1/dashboard/lists", "List dashboard lists"),
    EndpointTemplate(
        "/api/v1/dashboard/{dashboard_id}",
        "Get a specific dashboard by ID",
        "dashboard_id",
    ),
    EndpointTemplate(
        "/api/v1/dashboard/public/{token}",
        "Get a public dashboard by token",
        "token",
    ),
    # SLOs
    EndpointTemplate("/api/v1/slo", "List all SLOs"),
    EndpointTemplate(
        "/api/v1/slo/{slo_id}",
        "Get a specific SLO by ID",
        "slo_id",
    ),
    EndpointTemplate(
        "/api/v1/slo/{slo_id}/history",
        "Get SLO history",
        "slo_id",
    ),
    EndpointTemplate(
        "/api/v1/slo/{slo_id}/corrections",
        "Get SLO corrections",
        "slo_id",
    ),
    # Events
    EndpointTemplate(
        "/api/v1/events",
        "List events. Use 'start' and 'end' query params as Unix timestamps",
    ),
    EndpointTemplate(
        "/api/v1/events/{event_id}",
        "Get a specific event by ID",
        "event_id",
    ),
    # Incidents
    EndpointTemplate("/api/v2/incidents", "List all incidents"),
    EndpointTemplate(
        "/api/v2/incidents/{incident_id}",
        "Get a specific incident by ID",
        "incident_id",
    ),
    EndpointTemplate(
        "/api/v2/incidents/{incident_id}/attachments",
        "Get incident attachments",
        "incident_id",
    ),
    EndpointTemplate(
        "/api/v2/incidents/{incident_id}/relationships",
        "Get incident relationships",
        "incident_id",
    ),
    EndpointTemplate(
        "/api/v2/incidents/{incident_id}/timeline",
        "Get incident timeline",
        "incident_id",
    ),
    # Synthetics
    EndpointTemplate("/api/v1/synthetics/tests", "List all synthetic tests"),
    EndpointTemplate(
        "/api/v1/synthetics/tests/{public_id}",
        "Get a specific synthetic test by public ID",
        "public_id",
    ),
    EndpointTemplate(
        "/api/v1/synthetics/tests/{public_id}/results",
        "Get synthetic test results",
        "public_id",
    ),
    EndpointTemplate(
        "/api/v1/synthetics/tests/browser/{public_id}/results",
        "Get browser synthetic test results",
        "public_id",
    ),
    EndpointTemplate(
        "/api/v1/synthetics/tests/api/{public_id}/results",
        "Get API synthetic test results",
        "public_id",
    ),
    EndpointTemplate("/api/v1/synthetics/locations", "List synthetic test locations"),
    # Security Monitoring
    EndpointTemplate("/api/v2/security_monitoring/rules", "List security monitoring rules"),
    EndpointTemplate(
        "/api/v2/security_monitoring/rules/{rule_id}",
        "Get a specific security rule by ID",
        "rule_id",
    ),
    EndpointTemplate("/api/v2/security_monitoring/signals", "List security signals"),
    EndpointTemplate(
        "/api/v2/security_monitoring/signals/{signal_id}",
        "Get a specific security signal by ID",
        "signal_id",
    ),
    # Services (APM)
    EndpointTemplate("/api/v1/services", "List APM services"),
    EndpointTemplate(
        "/api/v1/services/{service_name}",
        "Get a specific APM service by name",
        "service_name",
    ),
    EndpointTemplate(
        "/api/v1/services/{service_name}/dependencies",
        "Get service dependencies",
        "service_name",
    ),
    EndpointTemplate("/api/v1/service_dependencies", "Get all service dependencies"),
    # Hosts
    EndpointTemplate("/api/v1/hosts", "List all hosts"),
    EndpointTemplate("/api/v1/hosts/totals", "Get host totals"),
    EndpointTemplate(
        "/api/v1/hosts/{hostname}",
        "Get a specific host by hostname",
        "hostname",
    ),
    # Usage
    EndpointTemplate("/api/v1/usage/summary", "Get usage summary"),
    EndpointTemplate("/api/v1/usage/billable-summary", "Get billable usage summary"),
    EndpointTemplate("/api/v1/usage/cost_by_org", "Get cost by organization"),
    EndpointTemplate("/api/v1/usage/estimated_cost", "Get estimated cost"),
    EndpointTemplate(
        "/api/v1/usage/{usage_type}",
        "Get usage for a specific type (e.g., hosts, logs, indexed_spans)",
        "usage_type",
    ),
    # Processes
    EndpointTemplate("/api/v1/processes", "List processes"),
    # Containers (v2 only)
    EndpointTemplate("/api/v2/containers", "List running containers"),
    EndpointTemplate("/api/v2/container_images", "List container images"),
    # Downtimes
    EndpointTemplate("/api/v1/downtime", "List scheduled downtimes"),
    EndpointTemplate(
        "/api/v1/downtime/{downtime_id}",
        "Get a specific downtime by ID",
        "downtime_id",
    ),
    EndpointTemplate(
        "/api/v2/monitor/{monitor_id}/downtime_matches",
        "Get active downtimes for a specific monitor",
        "monitor_id",
    ),
    # Tags
    EndpointTemplate("/api/v1/tags/hosts", "List all host tags"),
    EndpointTemplate(
        "/api/v1/tags/hosts/{hostname}",
        "Get tags for a specific host",
        "hostname",
    ),
    # Notebooks
    EndpointTemplate("/api/v1/notebooks", "List all notebooks"),
    EndpointTemplate(
        "/api/v1/notebooks/{notebook_id}",
        "Get a specific notebook by ID",
        "notebook_id",
    ),
    # Organization
    EndpointTemplate("/api/v1/org", "Get organization info"),
    EndpointTemplate(
        "/api/v1/org/{org_id}",
        "Get a specific organization by ID",
        "org_id",
    ),
    # Users
    EndpointTemplate("/api/v2/users", "List all users"),
    EndpointTemplate(
        "/api/v2/users/{user_id}",
        "Get a specific user by ID",
        "user_id",
    ),
    # Teams
    EndpointTemplate("/api/v2/teams", "List all teams"),
    EndpointTemplate(
        "/api/v2/teams/{team_id}",
        "Get a specific team by ID",
        "team_id",
    ),
    # Logs (prefer datadog/logs toolset when available)
    EndpointTemplate(
        "/api/v1/logs/config/indexes",
        "List log indexes. Prefer datadog/logs toolset when available",
    ),
    EndpointTemplate(
        "/api/v2/logs/events",
        "List log events. Use RFC3339 timestamps. Prefer datadog/logs toolset when available",
    ),
    # Metrics (prefer datadog/metrics toolset when available)
    EndpointTemplate(
        "/api/v1/metrics",
        "List metrics. Prefer datadog/metrics toolset when available",
    ),
    EndpointTemplate(
        "/api/v1/metrics/{metric_name}",
        "Get metric metadata. Prefer datadog/metrics toolset when available",
        "metric_name",
    ),
    EndpointTemplate(
        "/api/v1/query",
        "Query metrics. Use 'from' and 'to' as Unix timestamps. Prefer datadog/metrics toolset when available",
    ),
]

# POST endpoint templates for search/query operations
POST_ENDPOINT_TEMPLATES: List[EndpointTemplate] = [
    # Monitor search
    EndpointTemplate("/api/v1/monitor/search", "Search monitors with query"),
    # Dashboard lists
    EndpointTemplate("/api/v1/dashboard/lists", "Search dashboard lists"),
    # SLO search
    EndpointTemplate("/api/v1/slo/search", "Search SLOs with query"),
    # Events search (v2 only)
    EndpointTemplate(
        "/api/v2/events/search",
        "Search events with filters. Use RFC3339 timestamps",
    ),
    # Incidents search
    EndpointTemplate("/api/v2/incidents/search", "Search incidents with query"),
    # Synthetics search
    EndpointTemplate("/api/v1/synthetics/tests/search", "Search synthetic tests"),
    # Security monitoring search
    EndpointTemplate(
        "/api/v2/security_monitoring/rules/search",
        "Search security monitoring rules",
    ),
    EndpointTemplate(
        "/api/v2/security_monitoring/signals/search",
        "Search security signals",
    ),
    # Logs search (prefer datadog/logs toolset when available)
    EndpointTemplate(
        "/api/v2/logs/events/search",
        "Search logs. Use RFC3339 timestamps. Prefer datadog/logs toolset when available",
    ),
    EndpointTemplate(
        "/api/v2/logs/analytics/aggregate",
        "Aggregate logs. Do not include 'sort' parameter. Prefer datadog/logs toolset when available",
    ),
    # Spans search
    EndpointTemplate("/api/v2/spans/events/search", "Search APM spans"),
    # RUM search
    EndpointTemplate("/api/v2/rum/events/search", "Search RUM events"),
    # Audit search
    EndpointTemplate("/api/v2/audit/events/search", "Search audit events"),
    # Metrics query
    EndpointTemplate(
        "/api/v1/query",
        "Query metrics via POST. Prefer datadog/metrics toolset when available",
    ),
]

# Build lookup dictionaries for fast template validation
_GET_TEMPLATES_BY_PATH: Dict[str, EndpointTemplate] = {
    t.template: t for t in GET_ENDPOINT_TEMPLATES
}
_POST_TEMPLATES_BY_PATH: Dict[str, EndpointTemplate] = {
    t.template: t for t in POST_ENDPOINT_TEMPLATES
}

# Blacklisted path segments that indicate write operations (kept as safety net)
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


def get_valid_get_endpoint_templates() -> List[str]:
    """Return list of valid GET endpoint template strings for enum constraint."""
    return [t.template for t in GET_ENDPOINT_TEMPLATES]


def get_valid_post_endpoint_templates() -> List[str]:
    """Return list of valid POST endpoint template strings for enum constraint."""
    return [t.template for t in POST_ENDPOINT_TEMPLATES]


def build_endpoint_from_template(
    endpoint_template: str, resource_id: Optional[str], method: str = "GET"
) -> Tuple[bool, str, str]:
    """
    Build a concrete endpoint path from a template and optional resource_id.

    Returns:
        Tuple of (success, endpoint_or_error, description)
        - If success: (True, "/api/v1/monitor/12345", "Get a specific monitor by ID")
        - If failure: (False, "error message", "")
    """
    # Get the appropriate template lookup
    templates = _GET_TEMPLATES_BY_PATH if method == "GET" else _POST_TEMPLATES_BY_PATH

    # Validate template exists
    if endpoint_template not in templates:
        valid_templates = (
            get_valid_get_endpoint_templates()
            if method == "GET"
            else get_valid_post_endpoint_templates()
        )
        return (
            False,
            f"Invalid endpoint_template '{endpoint_template}'. Valid templates: {valid_templates}",
            "",
        )

    template_info = templates[endpoint_template]

    # Check if template has a placeholder
    has_placeholder = "{" in endpoint_template

    if has_placeholder:
        if not resource_id:
            return (
                False,
                f"endpoint_template '{endpoint_template}' requires resource_id parameter",
                "",
            )

        # Security: Validate resource_id to prevent path traversal and injection attacks
        # Check for path traversal sequences and URL-encoded variants
        resource_id_lower = resource_id.lower()
        if (
            "/" in resource_id
            or ".." in resource_id
            or "%2f" in resource_id_lower
            or "%2e" in resource_id_lower
        ):
            return (
                False,
                f"resource_id '{resource_id}' contains invalid characters (path traversal not allowed)",
                "",
            )

        # URL-encode the resource_id for safe path inclusion
        safe_resource_id = quote(resource_id, safe="")

        # Substitute the placeholder with resource_id using lambda to prevent
        # backreference injection (e.g., \1, \g<name> sequences)
        endpoint = re.sub(r"\{[^}]+\}", lambda _: safe_resource_id, endpoint_template)
    else:
        # Template has no placeholder - resource_id should be empty/None
        if resource_id:
            logging.warning(
                f"resource_id '{resource_id}' provided but template '{endpoint_template}' "
                "has no placeholder. Ignoring resource_id."
            )
        endpoint = endpoint_template

    # Safety check: verify no blacklisted segments
    path_lower = endpoint.lower()
    for segment in BLACKLISTED_SEGMENTS:
        if segment in path_lower:
            return False, f"Endpoint contains blacklisted operation '{segment}'", ""

    return True, endpoint, template_info.description


class DatadogGeneralToolset(Toolset):
    """General-purpose Datadog API toolset for read-only operations not covered by specialized toolsets."""

    config_classes: ClassVar[list[Type[DatadogGeneralConfig]]] = [DatadogGeneralConfig]

    dd_config: Optional[DatadogGeneralConfig] = None
    openapi_spec: Optional[Dict[str, Any]] = None

    def __init__(self):
        super().__init__(
            name="datadog/general",
            description="General-purpose Datadog API access for read-only operations including monitors, dashboards, SLOs, incidents, synthetics, logs, metrics, and more. Note: For logs and metrics, prefer using the specialized datadog/logs and datadog/metrics toolsets when available as they provide optimized functionality",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/datadog.svg",
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
                "Missing config for api_key, app_key, or api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
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
            return False, f"Invalid Datadog General configuration: {e}"

    def _perform_healthcheck(self, dd_config: DatadogGeneralConfig) -> Tuple[bool, str]:
        """Perform health check on Datadog API."""
        try:
            logging.info("Performing Datadog general API configuration healthcheck...")
            base_url = str(dd_config.api_url).rstrip("/")
            url = f"{base_url}/api/v1/validate"
            headers = get_headers(dd_config)

            data = execute_datadog_http_request(
                url=url,
                headers=headers,
                payload_or_params={},
                timeout=dd_config.timeout_seconds,
                method="GET",
            )

            if data.get("valid", False):
                logging.debug("Datadog general API health check completed successfully")
                return True, ""
            else:
                error_msg = "Datadog API key validation failed"
                logging.error(f"Datadog General health check failed: {error_msg}")
                return False, f"Datadog General health check failed: {error_msg}"

        except Exception as e:
            logging.exception("Failed during Datadog general API health check")
            return False, f"Datadog General health check failed: {e}"


class BaseDatadogGeneralTool(Tool):
    """Base class for general Datadog API tools."""

    toolset: "DatadogGeneralToolset"


class DatadogAPIGet(BaseDatadogGeneralTool):
    """Tool for making GET requests to Datadog API."""

    def __init__(self, toolset: "DatadogGeneralToolset"):
        # Build enum description from templates
        templates_desc = "\n".join(
            [f"  - {t.template}: {t.description}" for t in GET_ENDPOINT_TEMPLATES]
        )

        super().__init__(
            name="datadog_api_get",
            description="[datadog/general toolset] Make a GET request to a Datadog API endpoint for read-only operations",
            parameters={
                "endpoint_template": ToolParameter(
                    description=f"""The API endpoint template to call. Must be one of the valid templates below.
If the template contains a placeholder like {{monitor_id}}, you must provide the resource_id parameter.

Valid endpoint templates:
{templates_desc}""",
                    type="string",
                    required=True,
                    enum=get_valid_get_endpoint_templates(),
                ),
                "resource_id": ToolParameter(
                    description="The resource identifier to substitute into the endpoint template placeholder (e.g., monitor ID, dashboard ID, hostname). Required if the endpoint_template contains a placeholder like {monitor_id}.",
                    type="string",
                    required=False,
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
        params_return = params.copy()
        params_return["query_params"] = json.dumps(
            params.get("query_params", {}), indent=2
        )
        logging.info("=" * 60)
        logging.info("DatadogAPIGet Tool Invocation:")
        logging.info(f"  Description: {params.get('description', 'No description')}")
        logging.info(f"  Endpoint Template: {params.get('endpoint_template', '')}")
        logging.info(f"  Resource ID: {params.get('resource_id', 'None')}")
        logging.info(f"  Query Params: {params_return['query_params']}")
        logging.info("=" * 60)

        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params_return,
            )

        endpoint_template = params.get("endpoint_template", "")
        resource_id = params.get("resource_id")
        query_params = params.get("query_params", {})

        # Build endpoint from template and resource_id
        success, endpoint_or_error, _ = build_endpoint_from_template(
            endpoint_template, resource_id, method="GET"
        )
        if not success:
            logging.error(f"Endpoint validation failed: {endpoint_or_error}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Endpoint validation failed: {endpoint_or_error}",
                params=params_return,
            )

        endpoint = endpoint_or_error

        url = None
        try:
            # Build full URL (ensure no double slashes)
            base_url = str(self.toolset.dd_config.api_url).rstrip("/")
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
                timeout=self.toolset.dd_config.timeout_seconds,
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
                    params=params_return,
                )

            web_url = generate_datadog_general_url(
                self.toolset.dd_config,
                endpoint,
                query_params,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_str,
                params=params_return,
                url=web_url,
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
                    e, endpoint, "GET", str(self.toolset.dd_config.api_url)
                )
            else:
                error_msg = f"API error {e.status_code}: {str(e)}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                params=params_return,
                invocation=json.dumps({"url": url, "params": query_params})
                if url
                else None,
            )

        except Exception as e:
            logging.exception(f"Failed to query Datadog API: {params}", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params_return,
            )


class DatadogAPIPostSearch(BaseDatadogGeneralTool):
    """Tool for making POST requests to Datadog search/query endpoints."""

    def __init__(self, toolset: "DatadogGeneralToolset"):
        # Build enum description from templates
        templates_desc = "\n".join(
            [f"  - {t.template}: {t.description}" for t in POST_ENDPOINT_TEMPLATES]
        )

        super().__init__(
            name="datadog_api_post_search",
            description="[datadog/general toolset] Make a POST request to Datadog search/query endpoints for complex filtering",
            parameters={
                "endpoint_template": ToolParameter(
                    description=f"""The search API endpoint template to call. Must be one of the valid templates below.

Valid endpoint templates:
{templates_desc}""",
                    type="string",
                    required=True,
                    enum=get_valid_post_endpoint_templates(),
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

    def _body_to_query_params(self, body: dict) -> Optional[Dict[str, Any]]:
        body_query_params = {}
        if not isinstance(body, dict):
            return None
        if "filter" not in body:
            return None
        filter_data = body["filter"]
        if "from" in filter_data:
            try:
                body_query_params["from"] = int(filter_data["from"]) // 1000
            except (ValueError, TypeError):
                pass
        if "to" in filter_data:
            try:
                body_query_params["to"] = int(filter_data["to"]) // 1000
            except (ValueError, TypeError):
                pass
        if "query" in filter_data:
            body_query_params["query"] = filter_data["query"]

        if not body_query_params:
            return None

        return body_query_params

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """Execute the POST search request."""
        logging.info("=" * 60)
        logging.info("DatadogAPIPostSearch Tool Invocation:")
        logging.info(f"  Description: {params.get('description', 'No description')}")
        logging.info(f"  Endpoint Template: {params.get('endpoint_template', '')}")
        logging.info(f"  Body: {json.dumps(params.get('body', {}), indent=2)}")
        logging.info("=" * 60)

        if not self.toolset.dd_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )

        endpoint_template = params.get("endpoint_template", "")
        body = params.get("body", {})

        # Build endpoint from template (POST templates don't have placeholders currently)
        success, endpoint_or_error, _ = build_endpoint_from_template(
            endpoint_template, None, method="POST"
        )
        if not success:
            logging.error(f"Endpoint validation failed: {endpoint_or_error}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Endpoint validation failed: {endpoint_or_error}",
                params=params,
            )

        endpoint = endpoint_or_error

        url = None
        try:
            # Build full URL (ensure no double slashes)
            base_url = str(self.toolset.dd_config.api_url).rstrip("/")
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
                timeout=self.toolset.dd_config.timeout_seconds,
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

            body_query_params = self._body_to_query_params(body)
            web_url = generate_datadog_general_url(
                self.toolset.dd_config,
                endpoint,
                body_query_params,
            )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=response_str,
                params=params,
                url=web_url,
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
                    e, endpoint, "POST", str(self.toolset.dd_config.api_url)
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
        matching_get_endpoints: List[EndpointTemplate] = []
        matching_post_endpoints: List[EndpointTemplate] = []

        if search_regex:
            try:
                search_pattern = re.compile(search_regex, re.IGNORECASE)
            except re.error as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid regex pattern: {e}",
                    params=params,
                    url="",
                )
        else:
            search_pattern = None

        # Build list of matching GET endpoints
        for template in GET_ENDPOINT_TEMPLATES:
            if search_pattern and not search_pattern.search(template.template):
                continue
            matching_get_endpoints.append(template)

        # Build list of matching POST endpoints
        for template in POST_ENDPOINT_TEMPLATES:
            if search_pattern and not search_pattern.search(template.template):
                continue
            matching_post_endpoints.append(template)

        total_matches = len(matching_get_endpoints) + len(matching_post_endpoints)

        if total_matches == 0:
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=f"No endpoints found matching regex: {search_regex}",
                params=params,
            )

        # Format output
        output = ["Available Datadog API Endpoint Templates", "=" * 40]

        if search_regex:
            output.append(f"Filter: {search_regex}")
        output.append(f"Found: {total_matches} endpoints ({len(matching_get_endpoints)} GET, {len(matching_post_endpoints)} POST)")
        output.append("")

        # List GET endpoints
        if matching_get_endpoints:
            output.append("GET Endpoints:")
            output.append("-" * 40)
            for template in matching_get_endpoints:
                has_placeholder = "{" in template.template
                placeholder_note = f" (requires resource_id: {template.placeholder_name})" if has_placeholder else ""
                output.append(f"  {template.template}{placeholder_note}")
                output.append(f"    {template.description}")
            output.append("")

        # List POST endpoints
        if matching_post_endpoints:
            output.append("POST Endpoints (search/query operations):")
            output.append("-" * 40)
            for template in matching_post_endpoints:
                output.append(f"  {template.template}")
                output.append(f"    {template.description}")
            output.append("")

        output.append("Usage:")
        output.append("  • For GET: Use datadog_api_get with endpoint_template parameter")
        output.append("  • For POST: Use datadog_api_post_search with endpoint_template parameter")
        output.append("  • If template has {placeholder}, provide the resource_id parameter")
        output.append("")
        output.append("Examples:")
        output.append("  • datadog_api_get(endpoint_template='/api/v1/monitor', description='List monitors')")
        output.append("  • datadog_api_get(endpoint_template='/api/v1/monitor/{monitor_id}', resource_id='12345', description='Get monitor')")
        output.append("  • datadog_api_post_search(endpoint_template='/api/v1/monitor/search', body={...}, description='Search monitors')")
        output.append("")
        output.append("Search examples:")
        output.append("  • 'monitor' - find all monitor endpoints")
        output.append("  • 'logs|metrics' - find logs OR metrics endpoints")
        output.append("  • 'v2.*search$' - find all v2 search endpoints")
        output.append("  • 'security.*signals' - find security signals endpoints")

        doc_url = "https://docs.datadoghq.com/api/latest/"
        if search_regex:
            # URL encode the search parameter - spaces become + in query strings
            search_params = urlencode({"s": search_regex})
            doc_url = f"{doc_url}?{search_params}"

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="\n".join(output),
            params=params,
            url=doc_url,
        )
