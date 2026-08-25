import json
import os
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, cast
from urllib.parse import urljoin

import requests  # type: ignore
from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_rfc3339,
    toolset_name_for_one_liner,
)
from holmes.utils.header_rendering import render_header_templates
from holmes.utils.pydantic_utils import ToolsetConfig

# The F5 XC log/event APIs return at most 500 items per page.
MAX_QUERY_LIMIT = 500
DEFAULT_SECURITY_EVENTS_TIME_SPAN_SECONDS = 86400  # 24 hours
DEFAULT_REQUEST_LOGS_TIME_SPAN_SECONDS = 3600  # 1 hour

SECURITY_EVENT_TYPES = (
    "waf_sec_event",
    "bot_defense_sec_event",
    "api_sec_event",
    "svc_policy_sec_event",
)

QUERY_SYNTAX_DESCRIPTION = (
    "Label filter string using LogQL-like syntax: '{label=\"value\", other_label=~\"regex\"}'. "
    "Operators: = (equals), =~ (regex match), != (not equals), !~ (regex not match). "
    'Example: \'{vh_name="ves-io-http-loadbalancer-my-lb", sec_event_type=~"waf_sec_event|bot_defense_sec_event"}\'. '
    "The vh_name label requires the 'ves-io-http-loadbalancer-' prefix before the load balancer name. "
    "Omit this parameter to match everything in the namespace and time range."
)

START_TIME_DESCRIPTION = (
    "Start time, inclusive. RFC3339 datetime (e.g. '2026-08-03T10:00:00Z') "
    "or a negative integer for seconds relative to end_time (e.g. -3600 for the last hour). "
    "Defaults to -{default_span}."
)

END_TIME_DESCRIPTION = "End time, exclusive. RFC3339 datetime (e.g. '2026-08-03T12:00:00Z'). Defaults to now."


class F5XCConfig(ToolsetConfig):
    """Configuration for F5 Distributed Cloud (XC) API access.

    Example configuration:
    ```yaml
    api_url: "https://your-tenant.console.ves.volterra.io"
    api_token: "your-api-token"
    ```
    """

    api_url: str = Field(
        title="API URL",
        description="F5 Distributed Cloud tenant URL",
        examples=["https://your-tenant.console.ves.volterra.io"],
    )
    api_token: str = Field(
        title="API Token",
        description="F5 Distributed Cloud API token. API requests inherit the RBAC of the user "
        "that created the token; a token from a user with a read-only (monitor) role is recommended.",
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates when calling the F5 XC API",
    )
    timeout_seconds: int = Field(
        default=30,
        title="Timeout Seconds",
        description="Timeout in seconds for F5 XC API requests",
    )
    default_limit: int = Field(
        default=100,
        title="Default Query Limit",
        description=f"Default maximum number of events/logs returned by query tools when the LLM "
        f"does not specify a limit. Capped at {MAX_QUERY_LIMIT} (the API's per-page maximum).",
    )
    extra_headers: Optional[Dict[str, str]] = Field(
        default=None,
        title="Extra Headers",
        description="Optional extra HTTP headers rendered via Jinja2 templates. "
        "Supports request context (e.g. {{ request_context.headers['X-Tenant-Id'] }}) and env vars (e.g. {{ env.MY_TOKEN }}).",
        examples=[{"X-Custom-Header": "{{ env.MY_TOKEN }}"}],
    )


def parse_embedded_json_items(items: List[Any]) -> List[Any]:
    """The F5 XC log APIs return each event/log entry as a JSON-encoded string; decode them."""
    parsed = []
    for item in items:
        if isinstance(item, str):
            try:
                parsed.append(json.loads(item))
            except (json.JSONDecodeError, ValueError):
                parsed.append(item)
        else:
            parsed.append(item)
    return parsed


class F5XCToolset(Toolset):
    config_classes: ClassVar[list[Type[F5XCConfig]]] = [F5XCConfig]

    def __init__(self):
        super().__init__(
            name="f5xc",
            description="Query F5 Distributed Cloud (XC) for WAF security events, request logs, "
            "load balancer configuration and health",
            icon_url="https://cdn.simpleicons.org/f5",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/f5-distributed-cloud/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListNamespaces(self),
                ListHttpLoadBalancers(self),
                GetHttpLoadBalancer(self),
                ListOriginPools(self),
                QuerySecurityEvents(self),
                AggregateSecurityEvents(self),
                QueryRequestLogs(self),
            ],
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        """Check that the F5 XC configuration is valid and the API is reachable."""
        try:
            self.config = F5XCConfig(**config)
            return self._perform_health_check()
        except Exception as e:
            return False, f"Failed to validate F5 Distributed Cloud configuration: {str(e)}"

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Perform a health check by listing namespaces (cheap, authenticated call)."""
        try:
            self._make_api_request(
                method="GET",
                endpoint="/api/web/namespaces",
                timeout=10,
            )
            return (
                True,
                "F5 Distributed Cloud configuration is valid and the API is accessible.",
            )
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return (
                    False,
                    f"F5 Distributed Cloud authentication failed. Please check your API token "
                    f"(and that it has not expired). Full error: {e.response.status_code} - {e.response.text}",
                )
            elif e.response.status_code == 403:
                return (
                    False,
                    f"F5 Distributed Cloud access denied. Ensure the user that created the API token "
                    f"has at least a read-only (monitor) role. Full error: {e.response.status_code} - {e.response.text}",
                )
            else:
                return (
                    False,
                    f"F5 Distributed Cloud API returned an error: {e.response.status_code} - {e.response.text}",
                )
        except requests.exceptions.ConnectionError as e:
            return (
                False,
                f"Failed to connect to the F5 Distributed Cloud tenant at "
                f"{self.f5xc_config.api_url if self.config else 'unknown'}. Full error: {str(e)}",
            )
        except requests.exceptions.Timeout:
            return False, "F5 Distributed Cloud health check timed out"
        except Exception as e:
            return False, f"F5 Distributed Cloud health check failed: {str(e)}"

    @property
    def f5xc_config(self) -> F5XCConfig:
        return cast(F5XCConfig, self.config)

    def build_url(self, endpoint: str) -> str:
        return urljoin(
            self.f5xc_config.api_url.rstrip("/") + "/", endpoint.lstrip("/")
        )

    def _make_api_request(
        self,
        method: str,
        endpoint: str,
        query_params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
        timeout: Optional[int] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a request to the F5 XC API and return the parsed JSON response.

        Raises requests exceptions (HTTPError, ConnectionError, Timeout, ...) on failure.
        """
        url = self.build_url(endpoint)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"APIToken {self.f5xc_config.api_token}",
        }

        if self.f5xc_config.extra_headers:
            rendered = render_header_templates(
                extra_headers=self.f5xc_config.extra_headers,
                request_context=request_context,
                source_name=self.name,
            )
            if rendered:
                headers.update(rendered)

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query_params,
            json=json_body,
            timeout=timeout or self.f5xc_config.timeout_seconds,
            verify=self.f5xc_config.verify_ssl,
        )
        response.raise_for_status()
        return response.json()


class BaseF5XCTool(Tool, ABC):
    """Base class for F5 XC tools with shared request/error handling."""

    def __init__(self, toolset: F5XCToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _resolve_limit(self, params: dict) -> int:
        limit = params.get("limit") or self._toolset.f5xc_config.default_limit
        return min(int(limit), MAX_QUERY_LIMIT)

    def _error_result(
        self, e: Exception, params: dict, method: str, endpoint: str, detail: str
    ) -> StructuredToolResult:
        """Build a detailed error result so the LLM can self-correct."""
        url = self._toolset.build_url(endpoint)
        if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
            error = (
                f"F5 Distributed Cloud API error for {method} {url} ({detail}): "
                f"{e.response.status_code} - {e.response.text}"
            )
        elif isinstance(e, requests.exceptions.Timeout):
            error = f"F5 Distributed Cloud API request timed out for {method} {url} ({detail})"
        else:
            error = f"F5 Distributed Cloud API request failed for {method} {url} ({detail}): {str(e)}"
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=error,
            params=params,
            url=url,
        )


class ListNamespaces(BaseF5XCTool, JsonFilterMixin):
    def __init__(self, toolset: F5XCToolset):
        super().__init__(
            toolset=toolset,
            name="f5xc_list_namespaces",
            description="List all namespaces in the F5 Distributed Cloud tenant using GET /api/web/namespaces. "
            "Namespaces group application resources (load balancers, origin pools, WAF events).",
            parameters=JsonFilterMixin.extend_parameters({}),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        endpoint = "/api/web/namespaces"
        try:
            data = self._toolset._make_api_request(
                method="GET",
                endpoint=endpoint,
                request_context=context.request_context,
            )
        except Exception as e:
            return self._error_result(e, params, "GET", endpoint, "list namespaces")

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
            url=self._toolset.build_url(endpoint),
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List Namespaces"


class ListHttpLoadBalancers(BaseF5XCTool, JsonFilterMixin):
    def __init__(self, toolset: F5XCToolset):
        super().__init__(
            toolset=toolset,
            name="f5xc_list_http_load_balancers",
            description="List HTTP load balancers in an F5 Distributed Cloud namespace using "
            "GET /api/config/namespaces/{namespace}/http_loadbalancers. "
            "Set include_spec=true to also return each load balancer's full spec (domains, host_name CNAME, routes, WAF policy).",
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "namespace": ToolParameter(
                        description="The F5 XC namespace to list load balancers from. "
                        "Use f5xc_list_namespaces to discover namespaces.",
                        type="string",
                        required=True,
                    ),
                    "include_spec": ToolParameter(
                        description="If true, include the full spec of each load balancer (adds ?report_fields). "
                        "Default: false (names and metadata only, much smaller response).",
                        type="boolean",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        namespace = params["namespace"]
        endpoint = f"/api/config/namespaces/{namespace}/http_loadbalancers"
        query_params = {"report_fields": ""} if params.get("include_spec") else None
        try:
            data = self._toolset._make_api_request(
                method="GET",
                endpoint=endpoint,
                query_params=query_params,
                request_context=context.request_context,
            )
        except Exception as e:
            return self._error_result(
                e, params, "GET", endpoint, f"list HTTP load balancers in namespace '{namespace}'"
            )

        if not data.get("items"):
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data=f"No HTTP load balancers found in namespace '{namespace}'. "
                f"Use f5xc_list_namespaces to check other namespaces.",
                params=params,
                url=self._toolset.build_url(endpoint),
            )

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
            url=self._toolset.build_url(endpoint),
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        namespace = params.get("namespace", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List HTTP Load Balancers ({namespace})"


class GetHttpLoadBalancer(BaseF5XCTool, JsonFilterMixin):
    def __init__(self, toolset: F5XCToolset):
        super().__init__(
            toolset=toolset,
            name="f5xc_get_http_load_balancer",
            description="Get the full configuration of a single HTTP load balancer using "
            "GET /api/config/namespaces/{namespace}/http_loadbalancers/{name}. "
            "The spec includes domains, host_name (CNAME), dns_info, default_route_pools (origin pools), "
            "and the attached WAF policy.",
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "namespace": ToolParameter(
                        description="The F5 XC namespace of the load balancer",
                        type="string",
                        required=True,
                    ),
                    "name": ToolParameter(
                        description="The EXACT load balancer name from f5xc_list_http_load_balancers or provided "
                        "by the user. Do NOT guess or fabricate names.",
                        type="string",
                        required=True,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        namespace = params["namespace"]
        name = params["name"]
        endpoint = f"/api/config/namespaces/{namespace}/http_loadbalancers/{name}"
        try:
            data = self._toolset._make_api_request(
                method="GET",
                endpoint=endpoint,
                request_context=context.request_context,
            )
        except Exception as e:
            return self._error_result(
                e, params, "GET", endpoint, f"get HTTP load balancer '{name}' in namespace '{namespace}'"
            )

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
            url=self._toolset.build_url(endpoint),
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        namespace = params.get("namespace", "unknown")
        name = params.get("name", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get HTTP Load Balancer {name} ({namespace})"


class ListOriginPools(BaseF5XCTool, JsonFilterMixin):
    def __init__(self, toolset: F5XCToolset):
        super().__init__(
            toolset=toolset,
            name="f5xc_list_origin_pools",
            description="List origin pools (backend server groups) in an F5 Distributed Cloud namespace using "
            "GET /api/config/namespaces/{namespace}/origin_pools. "
            "Set include_spec=true to also return each pool's origin servers and health checks.",
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "namespace": ToolParameter(
                        description="The F5 XC namespace to list origin pools from",
                        type="string",
                        required=True,
                    ),
                    "include_spec": ToolParameter(
                        description="If true, include the full spec of each origin pool (adds ?report_fields). "
                        "Default: false (names and metadata only).",
                        type="boolean",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        namespace = params["namespace"]
        endpoint = f"/api/config/namespaces/{namespace}/origin_pools"
        query_params = {"report_fields": ""} if params.get("include_spec") else None
        try:
            data = self._toolset._make_api_request(
                method="GET",
                endpoint=endpoint,
                query_params=query_params,
                request_context=context.request_context,
            )
        except Exception as e:
            return self._error_result(
                e, params, "GET", endpoint, f"list origin pools in namespace '{namespace}'"
            )

        if not data.get("items"):
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data=f"No origin pools found in namespace '{namespace}'.",
                params=params,
                url=self._toolset.build_url(endpoint),
            )

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
            url=self._toolset.build_url(endpoint),
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        namespace = params.get("namespace", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List Origin Pools ({namespace})"


class QuerySecurityEvents(BaseF5XCTool, JsonFilterMixin):
    def __init__(self, toolset: F5XCToolset):
        super().__init__(
            toolset=toolset,
            name="f5xc_query_security_events",
            description="Query WAF, bot defense, API security and service policy security events using "
            "POST /api/data/namespaces/{namespace}/app_security/events. "
            "Returns matching security events (blocked/flagged requests) with attack details, source IPs, "
            "countries, and matched signatures. "
            f"Event types: {', '.join(SECURITY_EVENT_TYPES)}.",
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "namespace": ToolParameter(
                        description="The F5 XC namespace to query. Ignored when all_namespaces=true.",
                        type="string",
                        required=True,
                    ),
                    "query": ToolParameter(
                        description=QUERY_SYNTAX_DESCRIPTION
                        + ' Useful labels: vh_name, sec_event_type, src_ip, req_id. '
                        'Example for WAF events on one load balancer: \'{vh_name="ves-io-http-loadbalancer-my-lb", sec_event_type="waf_sec_event"}\'.',
                        type="string",
                        required=False,
                    ),
                    "start_time": ToolParameter(
                        description=START_TIME_DESCRIPTION.format(
                            default_span=DEFAULT_SECURITY_EVENTS_TIME_SPAN_SECONDS
                        )
                        + " (last 24 hours)",
                        type="string",
                        required=False,
                    ),
                    "end_time": ToolParameter(
                        description=END_TIME_DESCRIPTION,
                        type="string",
                        required=False,
                    ),
                    "limit": ToolParameter(
                        description=f"Maximum number of events to return (default: 100, max: {MAX_QUERY_LIMIT}). "
                        "If total_hits exceeds the returned count, narrow the query or time range, "
                        "or use f5xc_aggregate_security_events for an overview.",
                        type="integer",
                        required=False,
                    ),
                    "all_namespaces": ToolParameter(
                        description="If true, query security events across ALL namespaces in the tenant "
                        "(uses /api/data/namespaces/system/app_security/all_ns_events). Default: false.",
                        type="boolean",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        all_namespaces = bool(params.get("all_namespaces"))
        namespace = "system" if all_namespaces else params["namespace"]
        endpoint = (
            "/api/data/namespaces/system/app_security/all_ns_events"
            if all_namespaces
            else f"/api/data/namespaces/{namespace}/app_security/events"
        )
        start_time, end_time = process_timestamps_to_rfc3339(
            params.get("start_time"),
            params.get("end_time"),
            default_time_span_seconds=DEFAULT_SECURITY_EVENTS_TIME_SPAN_SECONDS,
        )
        limit = self._resolve_limit(params)

        body: Dict[str, Any] = {
            "namespace": namespace,
            "aggs": {},
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
            "sort": "DESCENDING",
        }
        if params.get("query"):
            body["query"] = params["query"]

        detail = (
            f"security events query={params.get('query') or '<all>'} "
            f"from {start_time} to {end_time} limit={limit}"
        )
        try:
            data = self._toolset._make_api_request(
                method="POST",
                endpoint=endpoint,
                json_body=body,
                request_context=context.request_context,
            )
        except Exception as e:
            return self._error_result(e, params, "POST", endpoint, detail)

        events = parse_embedded_json_items(data.get("events", []))
        total_hits = data.get("total_hits", len(events))

        if not events:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data=f"No security events found in namespace '{namespace}' "
                f"for query {params.get('query') or '<all events>'} between {start_time} and {end_time}. "
                "Try a wider time range, a different namespace, or all_namespaces=true.",
                params=params,
                url=self._toolset.build_url(endpoint),
            )

        response_data: Dict[str, Any] = {
            "total_hits": total_hits,
            "returned_events": len(events),
            "query": params.get("query"),
            "start_time": start_time,
            "end_time": end_time,
            "events": events,
        }
        try:
            if int(total_hits) > len(events):
                response_data["note"] = (
                    f"Only {len(events)} of {total_hits} matching events returned. "
                    "Narrow the query or time range, or use f5xc_aggregate_security_events for totals."
                )
        except (TypeError, ValueError):
            pass

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=response_data,
            params=params,
            url=self._toolset.build_url(endpoint),
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        scope = "all namespaces" if params.get("all_namespaces") else params.get("namespace", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Query Security Events ({scope})"


class AggregateSecurityEvents(BaseF5XCTool):
    def __init__(self, toolset: F5XCToolset):
        super().__init__(
            toolset=toolset,
            name="f5xc_aggregate_security_events",
            description="Aggregate (count) security events by a field using "
            "POST /api/data/namespaces/{namespace}/app_security/events/aggregation. "
            "Returns the top values of the field with event counts — ideal for questions like "
            "'which apps are under attack', 'top attacking IPs', or 'what attack types occurred'. "
            "Prefer this over f5xc_query_security_events for overview questions since it returns compact totals.",
            parameters={
                "namespace": ToolParameter(
                    description="The F5 XC namespace to query. Ignored when all_namespaces=true.",
                    type="string",
                    required=True,
                ),
                "field": ToolParameter(
                    description="Field to aggregate by, in UPPERCASE. Common fields: SEC_EVENT_TYPE (attack category), "
                    "VH_NAME (load balancer), SRC_IP (attacker IP), COUNTRY, REQ_PATH, SIGNATURE_NAME.",
                    type="string",
                    required=True,
                ),
                "topk": ToolParameter(
                    description="Number of top values to return (default: 10)",
                    type="integer",
                    required=False,
                ),
                "query": ToolParameter(
                    description=QUERY_SYNTAX_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "start_time": ToolParameter(
                    description=START_TIME_DESCRIPTION.format(
                        default_span=DEFAULT_SECURITY_EVENTS_TIME_SPAN_SECONDS
                    )
                    + " (last 24 hours)",
                    type="string",
                    required=False,
                ),
                "end_time": ToolParameter(
                    description=END_TIME_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "all_namespaces": ToolParameter(
                    description="If true, aggregate security events across ALL namespaces in the tenant. Default: false.",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        all_namespaces = bool(params.get("all_namespaces"))
        namespace = "system" if all_namespaces else params["namespace"]
        endpoint = (
            "/api/data/namespaces/system/app_security/all_ns_events/aggregation"
            if all_namespaces
            else f"/api/data/namespaces/{namespace}/app_security/events/aggregation"
        )
        field = params["field"].upper()
        topk = int(params.get("topk") or 10)
        start_time, end_time = process_timestamps_to_rfc3339(
            params.get("start_time"),
            params.get("end_time"),
            default_time_span_seconds=DEFAULT_SECURITY_EVENTS_TIME_SPAN_SECONDS,
        )

        aggregation_name = f"fieldAggregation_{field}_{topk}"
        body: Dict[str, Any] = {
            "namespace": namespace,
            "aggs": {
                aggregation_name: {
                    "field_aggregation": {"field": field, "topk": topk}
                }
            },
            "start_time": start_time,
            "end_time": end_time,
        }
        if params.get("query"):
            body["query"] = params["query"]

        detail = (
            f"security events aggregation by {field} (top {topk}) "
            f"query={params.get('query') or '<all>'} from {start_time} to {end_time}"
        )
        try:
            data = self._toolset._make_api_request(
                method="POST",
                endpoint=endpoint,
                json_body=body,
                request_context=context.request_context,
            )
        except Exception as e:
            return self._error_result(e, params, "POST", endpoint, detail)

        aggs = data.get("aggs", {})
        buckets = (
            aggs.get(aggregation_name, {}).get("field_aggregation", {}).get("buckets")
            or aggs.get(aggregation_name, {}).get("multi_field_aggregation", {}).get("buckets")
            or []
        )
        if not buckets:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data=f"No security events found to aggregate by {field} in namespace '{namespace}' "
                f"for query {params.get('query') or '<all events>'} between {start_time} and {end_time}.",
                params=params,
                url=self._toolset.build_url(endpoint),
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={
                "field": field,
                "start_time": start_time,
                "end_time": end_time,
                "query": params.get("query"),
                "buckets": buckets,
            },
            params=params,
            url=self._toolset.build_url(endpoint),
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        scope = "all namespaces" if params.get("all_namespaces") else params.get("namespace", "unknown")
        field = params.get("field", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Top Security Events by {field} ({scope})"


class QueryRequestLogs(BaseF5XCTool, JsonFilterMixin):
    def __init__(self, toolset: F5XCToolset):
        super().__init__(
            toolset=toolset,
            name="f5xc_query_request_logs",
            description="Query HTTP request (access) logs of load balancers using "
            "POST /api/data/namespaces/{namespace}/access_logs. "
            "Each log has method, req_path, rsp_code, src_ip, domain, timing breakdowns "
            "(e.g. rtt_upstream_seconds) and more. Useful for investigating errors, latency and traffic patterns.",
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "namespace": ToolParameter(
                        description="The F5 XC namespace to query",
                        type="string",
                        required=True,
                    ),
                    "query": ToolParameter(
                        description=QUERY_SYNTAX_DESCRIPTION
                        + ' Useful labels: vh_name, rsp_code (e.g. "403"), rsp_code_class ("2xx"|"3xx"|"4xx"|"5xx"), '
                        "method, src_ip, req_path, domain. "
                        'Example for errors on one load balancer: \'{vh_name="ves-io-http-loadbalancer-my-lb", rsp_code_class=~"4xx|5xx"}\'.',
                        type="string",
                        required=False,
                    ),
                    "start_time": ToolParameter(
                        description=START_TIME_DESCRIPTION.format(
                            default_span=DEFAULT_REQUEST_LOGS_TIME_SPAN_SECONDS
                        )
                        + " (last hour)",
                        type="string",
                        required=False,
                    ),
                    "end_time": ToolParameter(
                        description=END_TIME_DESCRIPTION,
                        type="string",
                        required=False,
                    ),
                    "limit": ToolParameter(
                        description=f"Maximum number of logs to return (default: 100, max: {MAX_QUERY_LIMIT}). "
                        "Request logs are high volume — prefer narrow queries and small limits.",
                        type="integer",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        namespace = params["namespace"]
        endpoint = f"/api/data/namespaces/{namespace}/access_logs"
        start_time, end_time = process_timestamps_to_rfc3339(
            params.get("start_time"),
            params.get("end_time"),
            default_time_span_seconds=DEFAULT_REQUEST_LOGS_TIME_SPAN_SECONDS,
        )
        limit = self._resolve_limit(params)

        body: Dict[str, Any] = {
            "namespace": namespace,
            "aggs": {},
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
            "sort": "DESCENDING",
        }
        if params.get("query"):
            body["query"] = params["query"]

        detail = (
            f"request logs query={params.get('query') or '<all>'} "
            f"from {start_time} to {end_time} limit={limit}"
        )
        try:
            data = self._toolset._make_api_request(
                method="POST",
                endpoint=endpoint,
                json_body=body,
                request_context=context.request_context,
            )
        except Exception as e:
            return self._error_result(e, params, "POST", endpoint, detail)

        # Unlike the security events API, the access logs API returns entries under "logs".
        logs = parse_embedded_json_items(data.get("logs", []))
        total_hits = data.get("total_hits", len(logs))

        if not logs:
            return StructuredToolResult(
                status=StructuredToolResultStatus.NO_DATA,
                data=f"No request logs found in namespace '{namespace}' "
                f"for query {params.get('query') or '<all logs>'} between {start_time} and {end_time}. "
                "Check the vh_name prefix ('ves-io-http-loadbalancer-<lb-name>') or widen the time range.",
                params=params,
                url=self._toolset.build_url(endpoint),
            )

        response_data: Dict[str, Any] = {
            "total_hits": total_hits,
            "returned_logs": len(logs),
            "query": params.get("query"),
            "start_time": start_time,
            "end_time": end_time,
            "logs": logs,
        }
        try:
            if int(total_hits) > len(logs):
                response_data["note"] = (
                    f"Only {len(logs)} of {total_hits} matching logs returned. "
                    "Narrow the query (e.g. filter by rsp_code_class or vh_name) or the time range."
                )
        except (TypeError, ValueError):
            pass

        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=response_data,
            params=params,
            url=self._toolset.build_url(endpoint),
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        namespace = params.get("namespace", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Query Request Logs ({namespace})"
