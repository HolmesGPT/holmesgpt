import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple, Type, Union
from urllib.parse import urljoin

import dateutil.parser
import requests  # type: ignore
from prometrix.connect.aws_connect import AWSPrometheusConnect
from prometrix.models.prometheus_config import PrometheusConfig as BasePrometheusConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from requests import RequestException

from holmes.common.env_vars import IS_OPENSHIFT, MAX_GRAPH_POINTS
from holmes.common.openshift import load_openshift_token
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
from holmes.core.tools_utils.token_counting import count_tool_response_tokens
from holmes.core.tools_utils.tool_context_window_limiter import get_pct_token_count
from holmes.plugins.toolsets.consts import STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_GRAPH_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.prometheus.utils import parse_duration_to_seconds
from holmes.plugins.toolsets.service_discovery import PrometheusDiscovery
from holmes.plugins.toolsets.utils import (
    get_param_or_raise,
    process_timestamps_to_rfc3339,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)
from holmes.utils.cache import TTLCache

PROMETHEUS_RULES_CACHE_KEY = "cached_prometheus_rules"
PROMETHEUS_METADATA_API_LIMIT = 100  # Default limit for Prometheus metadata APIs (series, labels, metadata) to prevent overwhelming responses
# Default timeout values for PromQL queries
DEFAULT_QUERY_TIMEOUT_SECONDS = 20
MAX_QUERY_TIMEOUT_SECONDS = 180
# Default timeout for metadata API calls (discovery endpoints)
DEFAULT_METADATA_TIMEOUT_SECONDS = 20
MAX_METADATA_TIMEOUT_SECONDS = 60
# Default time window for metadata APIs (in hours)
DEFAULT_METADATA_TIME_WINDOW_HRS = 1


class PrometheusConfig(BaseModel):
    """Prometheus toolset configuration.

    Deprecated config names (still accepted but not in schema):
    - default_metadata_time_window_hrs -> discover_metrics_from_last_hours
    - default_query_timeout_seconds -> query_timeout_seconds_default
    - max_query_timeout_seconds -> query_timeout_seconds_hard_max
    - default_metadata_timeout_seconds -> metadata_timeout_seconds_default
    - max_metadata_timeout_seconds -> metadata_timeout_seconds_hard_max
    - metrics_labels_time_window_hrs -> discover_metrics_from_last_hours
    - prometheus_ssl_enabled -> verify_ssl
    - metrics_labels_cache_duration_hrs (no longer used)
    - fetch_labels_with_labels_api (no longer used)
    - fetch_metadata_with_series_api (no longer used)
    """

    model_config = ConfigDict(extra="allow")

    # URL is optional because it can be set with an env var
    prometheus_url: Optional[str] = None

    # Discovery API time window - only return metrics with data in the last N hours
    discover_metrics_from_last_hours: int = DEFAULT_METADATA_TIME_WINDOW_HRS

    # Query timeout configuration
    query_timeout_seconds_default: int = DEFAULT_QUERY_TIMEOUT_SECONDS
    query_timeout_seconds_hard_max: int = MAX_QUERY_TIMEOUT_SECONDS

    # Metadata API timeout configuration
    metadata_timeout_seconds_default: int = DEFAULT_METADATA_TIMEOUT_SECONDS
    metadata_timeout_seconds_hard_max: int = MAX_METADATA_TIMEOUT_SECONDS

    tool_calls_return_data: bool = True
    headers: Dict = Field(default_factory=dict)
    rules_cache_duration_seconds: Optional[int] = 1800  # 30 minutes
    additional_labels: Optional[Dict[str, str]] = None
    verify_ssl: bool = True

    # Custom limit to the max number of tokens that a query result can take to proactively
    #   prevent token limit issues. Expressed in % of the model's context window.
    # This limit only overrides the global limit for all tools  (TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT)
    #   if it is lower.
    query_response_size_limit_pct: Optional[int] = None

    @field_validator("prometheus_url")
    def ensure_trailing_slash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.endswith("/"):
            return v + "/"
        return v

    @model_validator(mode="after")
    def validate_prom_config(self):
        # Handle deprecated config names passed as extra fields
        # These are accepted via extra="allow" but not defined in schema
        extra = self.model_extra or {}
        deprecated_with_replacement = []

        # Map of old names -> new names
        deprecated_mappings = {
            "default_metadata_time_window_hrs": "discover_metrics_from_last_hours",
            "default_query_timeout_seconds": "query_timeout_seconds_default",
            "max_query_timeout_seconds": "query_timeout_seconds_hard_max",
            "default_metadata_timeout_seconds": "metadata_timeout_seconds_default",
            "max_metadata_timeout_seconds": "metadata_timeout_seconds_hard_max",
            "metrics_labels_time_window_hrs": "discover_metrics_from_last_hours",
            "prometheus_ssl_enabled": "verify_ssl",
        }

        for old_name, new_name in deprecated_mappings.items():
            if old_name in extra:
                setattr(self, new_name, extra[old_name])
                deprecated_with_replacement.append(f"{old_name} -> {new_name}")

        if deprecated_with_replacement:
            logging.warning(
                f"Prometheus config uses deprecated names. Please update: "
                f"{', '.join(deprecated_with_replacement)}"
            )

        # Check for deprecated config values that no longer have any effect
        deprecated_no_effect = [
            name
            for name in [
                "metrics_labels_cache_duration_hrs",
                "fetch_labels_with_labels_api",
                "fetch_metadata_with_series_api",
            ]
            if name in extra
        ]

        if deprecated_no_effect:
            logging.warning(
                f"The following Prometheus config values are deprecated and have no effect: "
                f"{', '.join(deprecated_no_effect)}"
            )

        # If openshift is enabled, and the user didn't configure auth headers, we will try to load the token from the service account.
        if IS_OPENSHIFT:
            if self.headers.get("Authorization"):
                return self

            openshift_token = load_openshift_token()
            if openshift_token:
                logging.info("Using openshift token for prometheus toolset auth")
                self.headers["Authorization"] = f"Bearer {openshift_token}"

        return self

    def is_amp(self) -> bool:
        return False


class AMPConfig(PrometheusConfig):
    aws_access_key: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: str
    aws_service_name: str = "aps"
    verify_ssl: bool = False
    assume_role_arn: Optional[str] = None

    # Refresh the AWS client (and its STS creds) every N seconds (default: 15 minutes)
    refresh_interval_seconds: int = 900

    _aws_client: Optional[AWSPrometheusConnect] = None
    _aws_client_created_at: float = 0.0

    def is_amp(self) -> bool:
        return True

    def _should_refresh_client(self) -> bool:
        if not self._aws_client:
            return True
        return (
            time.time() - self._aws_client_created_at
        ) >= self.refresh_interval_seconds

    def get_aws_client(self) -> Optional[AWSPrometheusConnect]:
        if not self._aws_client or self._should_refresh_client():
            try:
                base_config = BasePrometheusConfig(
                    url=self.prometheus_url,
                    disable_ssl=not self.verify_ssl,
                    additional_labels=self.additional_labels,
                )
                self._aws_client = AWSPrometheusConnect(
                    access_key=self.aws_access_key,
                    secret_key=self.aws_secret_access_key,
                    token=None,
                    region=self.aws_region,
                    service_name=self.aws_service_name,
                    assume_role_arn=self.assume_role_arn,
                    config=base_config,
                )
                self._aws_client_created_at = time.time()
            except Exception:
                logging.exception("Failed to create/refresh AWS client")
                return self._aws_client
        return self._aws_client


class BasePrometheusTool(Tool):
    toolset: "PrometheusToolset"


def do_request(
    config,  # PrometheusConfig | AMPConfig
    url: str,
    params: Optional[Dict] = None,
    data: Optional[Dict] = None,
    timeout: int = 60,
    verify: Optional[bool] = None,
    headers: Optional[Dict] = None,
    method: str = "GET",
) -> requests.Response:
    """
    Route a request through either:
      - AWSPrometheusConnect (SigV4) when config is AMPConfig
      - plain requests otherwise

    method defaults to GET so callers can omit it for reads.
    """
    if verify is None:
        verify = config.verify_ssl
    if headers is None:
        headers = config.headers or {}

    if isinstance(config, AMPConfig):
        client = config.get_aws_client()  # cached AWSPrometheusConnect
        # Note: timeout parameter is not supported by prometrix's signed_request
        # AWS/AMP requests will not respect the timeout setting
        return client.signed_request(  # type: ignore
            method=method,
            url=url,
            data=data,
            params=params,
            verify=verify,
            headers=headers,
        )

    # Non-AMP: plain HTTP
    return requests.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        data=data,
        timeout=timeout,
        verify=verify,
    )


def result_has_data(result: Dict) -> bool:
    data = result.get("data", {})
    if len(data.get("result", [])) > 0:
        return True
    return False


def adjust_step_for_max_points(
    start_timestamp: str,
    end_timestamp: str,
    step: Optional[float] = None,
    max_points_override: Optional[float] = None,
) -> float:
    """
    Adjusts the step parameter to ensure the number of data points doesn't exceed max_points.

    Args:
        start_timestamp: RFC3339 formatted start time
        end_timestamp: RFC3339 formatted end time
        step: The requested step duration in seconds (None for auto-calculation)
        max_points_override: Optional override for max points (must be <= MAX_GRAPH_POINTS)

    Returns:
        Adjusted step value in seconds that ensures points <= max_points
    """
    # Use override if provided and valid, otherwise use default
    max_points = MAX_GRAPH_POINTS
    if max_points_override is not None:
        if max_points_override > MAX_GRAPH_POINTS:
            logging.warning(
                f"max_points override ({max_points_override}) exceeds system limit ({MAX_GRAPH_POINTS}), using {MAX_GRAPH_POINTS}"
            )
            max_points = MAX_GRAPH_POINTS
        elif max_points_override < 1:
            logging.warning(
                f"max_points override ({max_points_override}) is invalid, using default {MAX_GRAPH_POINTS}"
            )
            max_points = MAX_GRAPH_POINTS
        else:
            max_points = max_points_override
            logging.debug(f"Using max_points override: {max_points}")

    start_dt = dateutil.parser.parse(start_timestamp)
    end_dt = dateutil.parser.parse(end_timestamp)

    time_range_seconds = (end_dt - start_dt).total_seconds()

    # If no step provided, calculate a reasonable default
    # Aim for ~60 data points across the time range (1 per minute for hourly, etc)
    if step is None:
        step = max(1, time_range_seconds / 60)
        logging.debug(
            f"No step provided, defaulting to {step}s for {time_range_seconds}s range"
        )

    current_points = time_range_seconds / step

    # If current points exceed max, adjust the step
    if current_points > max_points:
        adjusted_step = time_range_seconds / max_points
        logging.info(
            f"Adjusting step from {step}s to {adjusted_step}s to limit points from {current_points:.0f} to {max_points}"
        )
        return adjusted_step

    return step


def add_prometheus_auth(prometheus_auth_header: Optional[str]) -> Dict[str, Any]:
    results = {}
    if prometheus_auth_header:
        results["Authorization"] = prometheus_auth_header
    return results


def create_data_summary_for_large_result(
    result_data: Dict, query: str, data_size_tokens: int, is_range_query: bool = False
) -> Dict[str, Any]:
    """
    Create a summary for large Prometheus results instead of returning full data.

    Args:
        result_data: The Prometheus data result
        query: The original PromQL query
        data_size_tokens: Size of the data in tokens
        is_range_query: Whether this is a range query (vs instant query)

    Returns:
        Dictionary with summary information and suggestions
    """
    if is_range_query:
        series_list = result_data.get("result", [])
        num_items = len(series_list)

        # Calculate exact total data points across all series
        total_points = 0
        for series in series_list:  # Iterate through ALL series for exact count
            points = len(series.get("values", []))
            total_points += points

        # Analyze label keys and their cardinality
        label_cardinality: Dict[str, set] = {}
        for series in series_list:
            metric = series.get("metric", {})
            for label_key, label_value in metric.items():
                if label_key not in label_cardinality:
                    label_cardinality[label_key] = set()
                label_cardinality[label_key].add(label_value)

        # Convert sets to counts for the summary
        label_summary = {
            label: len(values) for label, values in label_cardinality.items()
        }
        # Sort by cardinality (highest first) for better insights
        label_summary = dict(
            sorted(label_summary.items(), key=lambda x: x[1], reverse=True)
        )

        return {
            "message": f"Data too large to return ({data_size_tokens:,} tokens). Query returned {num_items} time series with {total_points:,} total data points.",
            "series_count": num_items,
            "total_data_points": total_points,
            "data_size_tokens": data_size_tokens,
            "label_cardinality": label_summary,
            "suggestion": f'Consider using topk({min(5, num_items)}, {query}) to limit results to the top {min(5, num_items)} series. To also capture remaining data as \'other\': topk({min(5, num_items)}, {query}) or label_replace((sum({query}) - sum(topk({min(5, num_items)}, {query}))), "pod", "other", "", "")',
        }
    else:
        # Instant query
        result_type = result_data.get("resultType", "")
        result_list = result_data.get("result", [])
        num_items = len(result_list)

        # Analyze label keys and their cardinality
        instant_label_cardinality: Dict[str, set] = {}
        for item in result_list:
            if isinstance(item, dict):
                metric = item.get("metric", {})
                for label_key, label_value in metric.items():
                    if label_key not in instant_label_cardinality:
                        instant_label_cardinality[label_key] = set()
                    instant_label_cardinality[label_key].add(label_value)

        # Convert sets to counts for the summary
        label_summary = {
            label: len(values) for label, values in instant_label_cardinality.items()
        }
        # Sort by cardinality (highest first) for better insights
        label_summary = dict(
            sorted(label_summary.items(), key=lambda x: x[1], reverse=True)
        )

        return {
            "message": f"Data too large to return ({data_size_tokens:,} tokens). Query returned {num_items} results.",
            "result_count": num_items,
            "result_type": result_type,
            "data_size_tokens": data_size_tokens,
            "label_cardinality": label_summary,
            "suggestion": f'Consider using topk({min(5, num_items)}, {query}) to limit results. To also capture remaining data as \'other\': topk({min(5, num_items)}, {query}) or label_replace((sum({query}) - sum(topk({min(5, num_items)}, {query}))), "instance", "other", "", "")',
        }


class MetricsBasedResponse(BaseModel):
    status: str
    error_message: Optional[str] = None
    data: Optional[str] = None
    tool_name: str
    description: str
    query: str
    start: Optional[str] = None
    end: Optional[str] = None
    step: Optional[float] = None
    output_type: Optional[str] = None
    data_summary: Optional[dict[str, Any]] = None


def create_structured_tool_result(
    params: dict, response: MetricsBasedResponse
) -> StructuredToolResult:
    status = StructuredToolResultStatus.SUCCESS
    error = None
    if response.error_message or response.status.lower() in ("failed", "error"):
        status = StructuredToolResultStatus.ERROR
        error = (
            response.error_message
            if response.error_message
            else "Unknown Prometheus error"
        )
    elif not response.data:
        status = StructuredToolResultStatus.NO_DATA

    return StructuredToolResult(
        status=status,
        data=response,
        params=params,
        error=error,
    )


class ListPrometheusRules(BasePrometheusTool):
    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="list_prometheus_rules",
            description="List all defined Prometheus rules (api/v1/rules). Will show the Prometheus rules description, expression and annotations",
            parameters={},
            toolset=toolset,
        )
        self._cache = None

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )
        if self.toolset.config.is_amp():
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Tool not supported in AMP",
                params=params,
            )
        if not self._cache and self.toolset.config.rules_cache_duration_seconds:
            self._cache = TTLCache(self.toolset.config.rules_cache_duration_seconds)  # type: ignore
        try:
            if self._cache:
                cached_rules = self._cache.get(PROMETHEUS_RULES_CACHE_KEY)
                if cached_rules:
                    logging.debug("rules returned from cache")

                    return StructuredToolResult(
                        status=StructuredToolResultStatus.SUCCESS,
                        data=cached_rules,
                        params=params,
                    )

            prometheus_url = self.toolset.config.prometheus_url

            rules_url = urljoin(prometheus_url, "api/v1/rules")

            rules_response = do_request(
                config=self.toolset.config,
                url=rules_url,
                params=params,
                timeout=40,
                verify=self.toolset.config.verify_ssl,
                headers=self.toolset.config.headers,
                method="GET",
            )
            rules_response.raise_for_status()
            data = rules_response.json()["data"]

            if self._cache:
                self._cache.set(PROMETHEUS_RULES_CACHE_KEY, data)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except requests.Timeout:
            logging.warning("Timeout while fetching prometheus rules", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Request timed out while fetching rules",
                params=params,
            )
        except RequestException as e:
            logging.warning("Failed to fetch prometheus rules", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while fetching rules: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.warning("Failed to process prometheus rules", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Fetch Rules"


class GetMetricNames(BasePrometheusTool):
    """Thin wrapper around /api/v1/label/__name__/values - the fastest way to discover metric names"""

    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="get_metric_names",
            description=(
                "Get list of metric names using /api/v1/label/__name__/values. "
                "FASTEST method for metric discovery when you need to explore available metrics. "
                f"Returns up to {PROMETHEUS_METADATA_API_LIMIT} unique metric names (limit={PROMETHEUS_METADATA_API_LIMIT}). If {PROMETHEUS_METADATA_API_LIMIT} results returned, more may exist - use a more specific filter. "
                f"ALWAYS use match[] parameter to filter metrics - without it you'll get random {PROMETHEUS_METADATA_API_LIMIT} metrics which is rarely useful. "
                "Note: Does not return metric metadata (type, description, labels). "
                "By default returns metrics active in the last 1 hour (configurable via default_metadata_time_window_hrs)."
            ),
            parameters={
                "match": ToolParameter(
                    description=(
                        "REQUIRED: PromQL selector to filter metrics. Use regex OR (|) to check multiple patterns in one call - much faster than multiple calls! Examples: "
                        "'{__name__=~\"node_cpu.*|node_memory.*|node_disk.*\"}' for all node resource metrics, "
                        "'{__name__=~\"container_cpu.*|container_memory.*|container_network.*\"}' for all container metrics, "
                        "'{__name__=~\"kube_pod.*|kube_deployment.*|kube_service.*\"}' for multiple Kubernetes object metrics, "
                        "'{__name__=~\".*cpu.*|.*memory.*|.*disk.*\"}' for all resource metrics, "
                        "'{namespace=~\"kube-system|default|monitoring\"}' for metrics from multiple namespaces, "
                        "'{job=~\"prometheus|node-exporter|kube-state-metrics\"}' for metrics from multiple jobs."
                    ),
                    type="string",
                    required=True,
                ),
                "start": ToolParameter(
                    description="Start timestamp (RFC3339 or Unix). Default: 1 hour ago",
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description="End timestamp (RFC3339 or Unix). Default: now",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )
        try:
            match_param = params.get("match")
            if not match_param:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Match parameter is required to filter metrics",
                    params=params,
                )

            url = urljoin(
                self.toolset.config.prometheus_url, "api/v1/label/__name__/values"
            )
            query_params = {
                "limit": str(PROMETHEUS_METADATA_API_LIMIT),
                "match[]": match_param,
            }

            # Add time parameters - use provided values or defaults
            if params.get("end"):
                query_params["end"] = params["end"]
            else:
                query_params["end"] = str(int(time.time()))

            if params.get("start"):
                query_params["start"] = params["start"]
            elif self.toolset.config.discover_metrics_from_last_hours:
                # Use default time window
                query_params["start"] = str(
                    int(time.time())
                    - (self.toolset.config.discover_metrics_from_last_hours * 3600)
                )

            response = do_request(
                config=self.toolset.config,
                url=url,
                params=query_params,
                timeout=self.toolset.config.metadata_timeout_seconds_default,
                verify=self.toolset.config.verify_ssl,
                headers=self.toolset.config.headers,
                method="GET",
            )
            response.raise_for_status()
            data = response.json()

            # Check if results were truncated
            if (
                "data" in data
                and isinstance(data["data"], list)
                and len(data["data"]) == PROMETHEUS_METADATA_API_LIMIT
            ):
                data["_truncated"] = True
                data["_message"] = (
                    f"Results truncated at limit={PROMETHEUS_METADATA_API_LIMIT}. Use a more specific match filter to see additional metrics."
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Metric Names"


class GetLabelValues(BasePrometheusTool):
    """Get values for a specific label across all metrics"""

    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="get_label_values",
            description=(
                "Get all values for a specific label using /api/v1/label/{label}/values. "
                "Use this to discover pods, namespaces, jobs, instances, etc. "
                f"Returns up to {PROMETHEUS_METADATA_API_LIMIT} unique values (limit={PROMETHEUS_METADATA_API_LIMIT}). If {PROMETHEUS_METADATA_API_LIMIT} results returned, more may exist - use match[] to filter. "
                "Supports optional match[] parameter to filter. "
                "By default returns values from metrics active in the last 1 hour (configurable via default_metadata_time_window_hrs)."
            ),
            parameters={
                "label": ToolParameter(
                    description="Label name to get values for (e.g., 'pod', 'namespace', 'job', 'instance')",
                    type="string",
                    required=True,
                ),
                "match": ToolParameter(
                    description=(
                        "Optional PromQL selector to filter (e.g., '{__name__=~\"kube.*\"}', "
                        "'{namespace=\"default\"}')."
                    ),
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description="Start timestamp (RFC3339 or Unix). Default: 1 hour ago",
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description="End timestamp (RFC3339 or Unix). Default: now",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )
        try:
            label = params.get("label")
            if not label:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Label parameter is required",
                    params=params,
                )

            url = urljoin(
                self.toolset.config.prometheus_url, f"api/v1/label/{label}/values"
            )
            query_params = {"limit": str(PROMETHEUS_METADATA_API_LIMIT)}
            if params.get("match"):
                query_params["match[]"] = params["match"]

            # Add time parameters - use provided values or defaults
            if params.get("end"):
                query_params["end"] = params["end"]
            else:
                query_params["end"] = str(int(time.time()))

            if params.get("start"):
                query_params["start"] = params["start"]
            elif self.toolset.config.discover_metrics_from_last_hours:
                # Use default time window
                query_params["start"] = str(
                    int(time.time())
                    - (self.toolset.config.discover_metrics_from_last_hours * 3600)
                )

            response = do_request(
                config=self.toolset.config,
                url=url,
                params=query_params,
                timeout=self.toolset.config.metadata_timeout_seconds_default,
                verify=self.toolset.config.verify_ssl,
                headers=self.toolset.config.headers,
                method="GET",
            )
            response.raise_for_status()
            data = response.json()

            # Check if results were truncated
            if (
                "data" in data
                and isinstance(data["data"], list)
                and len(data["data"]) == PROMETHEUS_METADATA_API_LIMIT
            ):
                data["_truncated"] = True
                data["_message"] = (
                    f"Results truncated at limit={PROMETHEUS_METADATA_API_LIMIT}. Use match[] parameter to filter label '{label}' values."
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        label = params.get("label", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get {label} Values"


class GetAllLabels(BasePrometheusTool):
    """Get all label names that exist in Prometheus"""

    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="get_all_labels",
            description=(
                "Get list of all label names using /api/v1/labels. "
                "Use this to discover what labels are available across all metrics. "
                f"Returns up to {PROMETHEUS_METADATA_API_LIMIT} label names (limit={PROMETHEUS_METADATA_API_LIMIT}). If {PROMETHEUS_METADATA_API_LIMIT} results returned, more may exist - use match[] to filter. "
                "Supports optional match[] parameter to filter. "
                "By default returns labels from metrics active in the last 1 hour (configurable via default_metadata_time_window_hrs)."
            ),
            parameters={
                "match": ToolParameter(
                    description=(
                        "Optional PromQL selector to filter (e.g., '{__name__=~\"kube.*\"}', "
                        "'{job=\"prometheus\"}')."
                    ),
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description="Start timestamp (RFC3339 or Unix). Default: 1 hour ago",
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description="End timestamp (RFC3339 or Unix). Default: now",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )
        try:
            url = urljoin(self.toolset.config.prometheus_url, "api/v1/labels")
            query_params = {"limit": str(PROMETHEUS_METADATA_API_LIMIT)}
            if params.get("match"):
                query_params["match[]"] = params["match"]

            # Add time parameters - use provided values or defaults
            if params.get("end"):
                query_params["end"] = params["end"]
            else:
                query_params["end"] = str(int(time.time()))

            if params.get("start"):
                query_params["start"] = params["start"]
            elif self.toolset.config.discover_metrics_from_last_hours:
                # Use default time window
                query_params["start"] = str(
                    int(time.time())
                    - (self.toolset.config.discover_metrics_from_last_hours * 3600)
                )

            response = do_request(
                config=self.toolset.config,
                url=url,
                params=query_params,
                timeout=self.toolset.config.metadata_timeout_seconds_default,
                verify=self.toolset.config.verify_ssl,
                headers=self.toolset.config.headers,
                method="GET",
            )
            response.raise_for_status()
            data = response.json()

            # Check if results were truncated
            if (
                "data" in data
                and isinstance(data["data"], list)
                and len(data["data"]) == PROMETHEUS_METADATA_API_LIMIT
            ):
                data["_truncated"] = True
                data["_message"] = (
                    f"Results truncated at limit={PROMETHEUS_METADATA_API_LIMIT}. Use match[] parameter to filter labels."
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get All Labels"


class GetSeries(BasePrometheusTool):
    """Get time series matching a selector"""

    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="get_series",
            description=(
                "Get time series using /api/v1/series. "
                "Returns label sets for all time series matching the selector. "
                "SLOWER than other discovery methods - use only when you need full label sets. "
                f"Returns up to {PROMETHEUS_METADATA_API_LIMIT} series (limit={PROMETHEUS_METADATA_API_LIMIT}). If {PROMETHEUS_METADATA_API_LIMIT} results returned, more series exist - use more specific selector. "
                "Requires match[] parameter with PromQL selector. "
                "By default returns series active in the last 1 hour (configurable via default_metadata_time_window_hrs)."
            ),
            parameters={
                "match": ToolParameter(
                    description=(
                        "PromQL selector to match series (e.g., 'up', 'node_cpu_seconds_total', "
                        "'{__name__=~\"node.*\"}', '{job=\"prometheus\"}', "
                        '\'{__name__="up",job="prometheus"}\').'
                    ),
                    type="string",
                    required=True,
                ),
                "start": ToolParameter(
                    description="Start timestamp (RFC3339 or Unix). Default: 1 hour ago",
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description="End timestamp (RFC3339 or Unix). Default: now",
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )
        try:
            match = params.get("match")
            if not match:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error="Match parameter is required",
                    params=params,
                )

            url = urljoin(self.toolset.config.prometheus_url, "api/v1/series")
            query_params = {
                "match[]": match,
                "limit": str(PROMETHEUS_METADATA_API_LIMIT),
            }

            # Add time parameters - use provided values or defaults
            if params.get("end"):
                query_params["end"] = params["end"]
            else:
                query_params["end"] = str(int(time.time()))

            if params.get("start"):
                query_params["start"] = params["start"]
            elif self.toolset.config.discover_metrics_from_last_hours:
                # Use default time window
                query_params["start"] = str(
                    int(time.time())
                    - (self.toolset.config.discover_metrics_from_last_hours * 3600)
                )

            response = do_request(
                config=self.toolset.config,
                url=url,
                params=query_params,
                timeout=self.toolset.config.metadata_timeout_seconds_default,
                verify=self.toolset.config.verify_ssl,
                headers=self.toolset.config.headers,
                method="GET",
            )
            response.raise_for_status()
            data = response.json()

            # Check if results were truncated
            if (
                "data" in data
                and isinstance(data["data"], list)
                and len(data["data"]) == PROMETHEUS_METADATA_API_LIMIT
            ):
                data["_truncated"] = True
                data["_message"] = (
                    f"Results truncated at limit={PROMETHEUS_METADATA_API_LIMIT}. Use a more specific match selector to see additional series."
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Get Series"


class GetMetricMetadata(BasePrometheusTool):
    """Get metadata (type, description, unit) for metrics"""

    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="get_metric_metadata",
            description=(
                "Get metric metadata using /api/v1/metadata. "
                "Returns type, help text, and unit for metrics. "
                "Use after discovering metric names to get their descriptions. "
                f"Returns up to {PROMETHEUS_METADATA_API_LIMIT} metrics (limit={PROMETHEUS_METADATA_API_LIMIT}). If {PROMETHEUS_METADATA_API_LIMIT} results returned, more may exist - filter by specific metric name. "
                "Supports optional metric name filter."
            ),
            parameters={
                "metric": ToolParameter(
                    description=(
                        "Optional metric name to filter (e.g., 'up', 'node_cpu_seconds_total'). "
                        "If not provided, returns metadata for all metrics."
                    ),
                    type="string",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )
        try:
            url = urljoin(self.toolset.config.prometheus_url, "api/v1/metadata")
            query_params = {"limit": str(PROMETHEUS_METADATA_API_LIMIT)}

            if params.get("metric"):
                query_params["metric"] = params["metric"]

            response = do_request(
                config=self.toolset.config,
                url=url,
                params=query_params,
                timeout=self.toolset.config.metadata_timeout_seconds_default,
                verify=self.toolset.config.verify_ssl,
                headers=self.toolset.config.headers,
                method="GET",
            )
            response.raise_for_status()
            data = response.json()

            # Check if results were truncated (metadata endpoint returns a dict, not a list)
            if (
                "data" in data
                and isinstance(data["data"], dict)
                and len(data["data"]) == PROMETHEUS_METADATA_API_LIMIT
            ):
                data["_truncated"] = True
                data["_message"] = (
                    f"Results truncated at limit={PROMETHEUS_METADATA_API_LIMIT}. Use metric parameter to filter by specific metric name."
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        metric = params.get("metric", "all")
        return (
            f"{toolset_name_for_one_liner(self.toolset.name)}: Get Metadata ({metric})"
        )


class ExecuteInstantQuery(BasePrometheusTool):
    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="execute_prometheus_instant_query",
            description=(
                f"Execute an instant PromQL query (single point in time). "
                f"Default timeout is {DEFAULT_QUERY_TIMEOUT_SECONDS} seconds "
                f"but can be increased up to {MAX_QUERY_TIMEOUT_SECONDS} seconds for complex/slow queries."
            ),
            parameters={
                "query": ToolParameter(
                    description="The PromQL query",
                    type="string",
                    required=True,
                ),
                "description": ToolParameter(
                    description="Describes the query",
                    type="string",
                    required=True,
                ),
                "timeout": ToolParameter(
                    description=(
                        f"Query timeout in seconds. Default: {DEFAULT_QUERY_TIMEOUT_SECONDS}. "
                        f"Maximum: {MAX_QUERY_TIMEOUT_SECONDS}. "
                        f"Increase for complex queries that may take longer."
                    ),
                    type="number",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )
        try:
            query = params.get("query", "")
            description = params.get("description", "")

            url = urljoin(self.toolset.config.prometheus_url, "api/v1/query")

            payload = {"query": query}

            # Get timeout parameter and enforce limits
            default_timeout = self.toolset.config.query_timeout_seconds_default
            max_timeout = self.toolset.config.query_timeout_seconds_hard_max
            timeout = params.get("timeout", default_timeout)
            if timeout > max_timeout:
                timeout = max_timeout
                logging.warning(
                    f"Timeout requested ({params.get('timeout')}) exceeds maximum ({max_timeout}s), using {max_timeout}s"
                )
            elif timeout < 1:
                timeout = default_timeout  # Min 1 second, but use default if invalid

            response = do_request(
                config=self.toolset.config,
                url=url,
                headers=self.toolset.config.headers,
                data=payload,
                timeout=timeout,
                verify=self.toolset.config.verify_ssl,
                method="POST",
            )

            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                error_message = None
                if status == "success" and not result_has_data(data):
                    status = "Failed"
                    error_message = (
                        "The prometheus query returned no result. Is the query correct?"
                    )
                response_data = MetricsBasedResponse(
                    status=status,
                    error_message=error_message,
                    tool_name=self.name,
                    description=description,
                    query=query,
                )
                structured_tool_result: StructuredToolResult
                # Check if data should be included based on size
                if self.toolset.config.tool_calls_return_data:
                    result_data = data.get("data", {})
                    response_data.data = result_data

                    structured_tool_result = create_structured_tool_result(
                        params=params, response=response_data
                    )
                    tool_call_id = context.tool_call_id
                    tool_name = context.tool_name
                    token_count = count_tool_response_tokens(
                        llm=context.llm,
                        structured_tool_result=structured_tool_result,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                    )

                    token_limit = context.max_token_count
                    if self.toolset.config.query_response_size_limit_pct:
                        custom_token_limit = get_pct_token_count(
                            percent_of_total_context_window=self.toolset.config.query_response_size_limit_pct,
                            llm=context.llm,
                        )
                        if custom_token_limit < token_limit:
                            token_limit = custom_token_limit

                    # Provide summary if data is too large
                    if token_count > token_limit:
                        response_data.data = None
                        response_data.data_summary = (
                            create_data_summary_for_large_result(
                                result_data,
                                query,
                                token_count,
                                is_range_query=False,
                            )
                        )
                        logging.info(
                            f"Prometheus instant query returned large dataset: "
                            f"{response_data.data_summary.get('result_count', 0)} results, "
                            f"{token_count:,} tokens (limit: {token_limit:,}). "
                            f"Returning summary instead of full data."
                        )
                        # Also add token info to the summary for debugging
                        response_data.data_summary["_debug_info"] = (
                            f"Data size: {token_count:,} tokens exceeded limit of {token_limit:,} tokens"
                        )
                    else:
                        response_data.data = result_data

                structured_tool_result = create_structured_tool_result(
                    params=params, response=response_data
                )
                return structured_tool_result

            # Handle known Prometheus error status codes
            error_msg = "Unknown error occurred"
            if response.status_code in [400, 429]:
                try:
                    error_data = response.json()
                    error_msg = error_data.get(
                        "error", error_data.get("message", str(response.content))
                    )
                except json.JSONDecodeError:
                    pass
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Query execution failed. HTTP {response.status_code}: {error_msg}",
                    params=params,
                )

            # For other status codes, just return the status code and content
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Query execution failed with unexpected status code: {response.status_code}. Response: {str(response.content)}",
                params=params,
            )

        except RequestException as e:
            logging.info("Failed to connect to Prometheus", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Connection error to Prometheus: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.info("Failed to connect to Prometheus", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error executing query: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        description = params.get("description", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Query ({description})"


class ExecuteRangeQuery(BasePrometheusTool):
    def __init__(self, toolset: "PrometheusToolset"):
        super().__init__(
            name="execute_prometheus_range_query",
            description=(
                f"Generates a graph and Execute a PromQL range query. "
                f"Default timeout is {DEFAULT_QUERY_TIMEOUT_SECONDS} seconds "
                f"but can be increased up to {MAX_QUERY_TIMEOUT_SECONDS} seconds for complex/slow queries. "
                f"Default time range is last 1 hour."
            ),
            parameters={
                "query": ToolParameter(
                    description="The PromQL query",
                    type="string",
                    required=True,
                ),
                "description": ToolParameter(
                    description="Describes the query",
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
                "step": ToolParameter(
                    description="Query resolution step width in duration format or float number of seconds",
                    type="number",
                    required=False,
                ),
                "output_type": ToolParameter(
                    description="Specifies how to interpret the Prometheus result. Use 'Plain' for raw values, 'Bytes' to format byte values, 'Percentage' to scale 0–1 values into 0–100%, or 'CPUUsage' to convert values to cores (e.g., 500 becomes 500m, 2000 becomes 2).",
                    type="string",
                    required=True,
                ),
                "timeout": ToolParameter(
                    description=(
                        f"Query timeout in seconds. Default: {DEFAULT_QUERY_TIMEOUT_SECONDS}. "
                        f"Maximum: {MAX_QUERY_TIMEOUT_SECONDS}. "
                        f"Increase for complex queries that may take longer."
                    ),
                    type="number",
                    required=False,
                ),
                "max_points": ToolParameter(
                    description=(
                        f"Maximum number of data points to return. Default: {int(MAX_GRAPH_POINTS)}. "
                        f"Can be reduced to get fewer data points (e.g., 50 for simpler graphs). "
                        f"Cannot exceed system limit of {int(MAX_GRAPH_POINTS)}. "
                        f"If your query would return more points than this limit, the step will be automatically adjusted."
                    ),
                    type="number",
                    required=False,
                ),
            },
            toolset=toolset,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        if not self.toolset.config or not self.toolset.config.prometheus_url:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Prometheus is not configured. Prometheus URL is missing",
                params=params,
            )

        try:
            url = urljoin(self.toolset.config.prometheus_url, "api/v1/query_range")

            query = get_param_or_raise(params, "query")
            (start, end) = process_timestamps_to_rfc3339(
                start_timestamp=params.get("start"),
                end_timestamp=params.get("end"),
                default_time_span_seconds=DEFAULT_GRAPH_TIME_SPAN_SECONDS,
            )
            step = parse_duration_to_seconds(params.get("step"))
            max_points = params.get(
                "max_points"
            )  # Get the optional max_points parameter

            # adjust_step_for_max_points handles None case and converts to float
            step = adjust_step_for_max_points(
                start_timestamp=start,
                end_timestamp=end,
                step=step,
                max_points_override=max_points,
            )

            description = params.get("description", "")
            output_type = params.get("output_type", "Plain")
            payload = {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
            }

            # Get timeout parameter and enforce limits
            default_timeout = self.toolset.config.query_timeout_seconds_default
            max_timeout = self.toolset.config.query_timeout_seconds_hard_max
            timeout = params.get("timeout", default_timeout)
            if timeout > max_timeout:
                timeout = max_timeout
                logging.warning(
                    f"Timeout requested ({params.get('timeout')}) exceeds maximum ({max_timeout}s), using {max_timeout}s"
                )
            elif timeout < 1:
                timeout = default_timeout  # Min 1 second, but use default if invalid

            response = do_request(
                config=self.toolset.config,
                url=url,
                headers=self.toolset.config.headers,
                data=payload,
                timeout=timeout,
                verify=self.toolset.config.verify_ssl,
                method="POST",
            )

            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                error_message = None
                if status == "success" and not result_has_data(data):
                    status = "Failed"
                    error_message = (
                        "The prometheus query returned no result. Is the query correct?"
                    )
                response_data = MetricsBasedResponse(
                    status=status,
                    error_message=error_message,
                    tool_name=self.name,
                    description=description,
                    query=query,
                    start=start,
                    end=end,
                    step=step,
                    output_type=output_type,
                )

                structured_tool_result: StructuredToolResult

                # Check if data should be included based on size
                if self.toolset.config.tool_calls_return_data:
                    result_data = data.get("data", {})
                    response_data.data = result_data
                    structured_tool_result = create_structured_tool_result(
                        params=params, response=response_data
                    )

                    tool_call_id = context.tool_call_id
                    tool_name = context.tool_name
                    token_count = count_tool_response_tokens(
                        llm=context.llm,
                        structured_tool_result=structured_tool_result,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                    )

                    token_limit = context.max_token_count
                    if self.toolset.config.query_response_size_limit_pct:
                        custom_token_limit = get_pct_token_count(
                            percent_of_total_context_window=self.toolset.config.query_response_size_limit_pct,
                            llm=context.llm,
                        )
                        if custom_token_limit < token_limit:
                            token_limit = custom_token_limit

                    # Provide summary if data is too large
                    if token_count > token_limit:
                        response_data.data = None
                        response_data.data_summary = (
                            create_data_summary_for_large_result(
                                result_data, query, token_count, is_range_query=True
                            )
                        )
                        logging.info(
                            f"Prometheus range query returned large dataset: "
                            f"{response_data.data_summary.get('series_count', 0)} series, "
                            f"{token_count:,} tokens (limit: {token_limit:,}). "
                            f"Returning summary instead of full data."
                        )
                        # Also add character info to the summary for debugging
                        response_data.data_summary["_debug_info"] = (
                            f"Data size: {token_count:,} tokens exceeded limit of {token_limit:,} tokens"
                        )
                    else:
                        response_data.data = result_data

                structured_tool_result = create_structured_tool_result(
                    params=params, response=response_data
                )

                return structured_tool_result

            error_msg = "Unknown error occurred"
            if response.status_code in [400, 429]:
                try:
                    error_data = response.json()
                    error_msg = error_data.get(
                        "error", error_data.get("message", str(response.content))
                    )
                except json.JSONDecodeError:
                    pass
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Query execution failed. HTTP {response.status_code}: {error_msg}",
                    params=params,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Query execution failed with unexpected status code: {response.status_code}. Response: {str(response.content)}",
                params=params,
            )

        except RequestException as e:
            logging.info("Failed to connect to Prometheus", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Connection error to Prometheus: {str(e)}",
                params=params,
            )
        except Exception as e:
            logging.info("Failed to connect to Prometheus", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error executing query: {str(e)}",
                params=params,
            )

    def get_parameterized_one_liner(self, params) -> str:
        description = params.get("description", "")
        return f"{toolset_name_for_one_liner(self.toolset.name)}: Query ({description})"


class PrometheusToolset(Toolset):
    config: Optional[Union[PrometheusConfig, AMPConfig]] = None

    def __init__(self):
        super().__init__(
            name="prometheus/metrics",
            description="Prometheus integration to fetch metadata and execute PromQL queries",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/prometheus/",
            icon_url="https://upload.wikimedia.org/wikipedia/commons/3/38/Prometheus_software_logo.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                ListPrometheusRules(toolset=self),
                GetMetricNames(toolset=self),
                GetLabelValues(toolset=self),
                GetAllLabels(toolset=self),
                GetSeries(toolset=self),
                GetMetricMetadata(toolset=self),
                ExecuteInstantQuery(toolset=self),
                ExecuteRangeQuery(toolset=self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
        )
        self._reload_llm_instructions()

    def _reload_llm_instructions(self):
        template_file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "prometheus_instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")

    def determine_prometheus_class(
        self, config: dict[str, Any]
    ) -> Type[Union[PrometheusConfig, AMPConfig]]:
        has_aws_fields = "aws_region" in config
        return AMPConfig if has_aws_fields else PrometheusConfig

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            if config:
                config_cls = self.determine_prometheus_class(config)
                self.config = config_cls(**config)  # type: ignore

                self._reload_llm_instructions()
                return self._is_healthy()
        except Exception:
            logging.exception("Failed to create prometheus config")
            return False, "Failed to create prometheus config"
        try:
            prometheus_url = os.environ.get("PROMETHEUS_URL")
            if not prometheus_url:
                prometheus_url = self.auto_detect_prometheus_url()
                if not prometheus_url:
                    return (
                        False,
                        "Unable to auto-detect prometheus. Define prometheus_url in the configuration for tool prometheus/metrics",
                    )

            self.config = PrometheusConfig(
                prometheus_url=prometheus_url,
                headers=add_prometheus_auth(os.environ.get("PROMETHEUS_AUTH_HEADER")),
            )
            logging.info(f"Prometheus auto discovered at url {prometheus_url}")
            self._reload_llm_instructions()
            return self._is_healthy()
        except Exception as e:
            logging.exception("Failed to set up prometheus")
            return False, str(e)

    def auto_detect_prometheus_url(self) -> Optional[str]:
        url: Optional[str] = PrometheusDiscovery.find_prometheus_url()
        if not url:
            url = PrometheusDiscovery.find_vm_url()

        return url

    def _is_healthy(self) -> Tuple[bool, str]:
        if (
            not hasattr(self, "config")
            or not self.config
            or not self.config.prometheus_url
        ):
            return (
                False,
                f"Toolset {self.name} failed to initialize because prometheus is not configured correctly",
            )

        url = urljoin(self.config.prometheus_url, "api/v1/query?query=up")
        try:
            response = do_request(
                config=self.config,
                url=url,
                headers=self.config.headers,
                timeout=10,
                verify=self.config.verify_ssl,
                method="GET",
            )

            if response.status_code == 200:
                return True, ""
            else:
                return (
                    False,
                    f"Failed to connect to Prometheus at {url}: HTTP {response.status_code}",
                )

        except Exception as e:
            logging.debug("Failed to initialize Prometheus", exc_info=True)
            return (
                False,
                f"Failed to initialize using url={url}. Unexpected error: {str(e)}",
            )

    def get_example_config(self):
        example_config = PrometheusConfig(
            prometheus_url="http://prometheus-server.monitoring.svc.cluster.local:9090",
            headers={"Authorization": "Basic <base64_encoded_credentials>"},
            discover_metrics_from_last_hours=1,
            query_timeout_seconds_default=20,
            query_timeout_seconds_hard_max=180,
            verify_ssl=True,
        )
        return example_config.model_dump()
