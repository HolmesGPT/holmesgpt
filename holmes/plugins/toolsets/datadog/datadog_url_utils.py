import re
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

from holmes.plugins.toolsets.datadog.datadog_api import convert_api_url_to_app_url
from holmes.plugins.toolsets.datadog.datadog_models import (
    DatadogGeneralConfig,
    DatadogLogsConfig,
    DatadogMetricsConfig,
    DatadogTracesConfig,
)


def generate_datadog_metrics_explorer_url(
    dd_config: DatadogMetricsConfig,
    query: str,
    from_time: int,
    to_time: int,
) -> str:
    base_url = convert_api_url_to_app_url(dd_config.api_url)

    params = {
        "query": query,
        "from_ts": from_time * 1000,  # seconds -> ms
        "to_ts": to_time * 1000,  # seconds -> ms
        "live": "true",
    }

    return f"{base_url}/metric/explorer?{urlencode(params)}"


def generate_datadog_metrics_list_url(
    dd_config: DatadogMetricsConfig,
    from_time: int,
    host: Optional[str] = None,
    tag_filter: Optional[str] = None,
    metric_filter: Optional[str] = None,
) -> str:
    base_url = convert_api_url_to_app_url(dd_config.api_url)

    params = {}
    if metric_filter:
        params["filter"] = metric_filter

    if host:
        params["host"] = host
    if tag_filter:
        params["tag_filter"] = tag_filter

    qs = urlencode(params) if params else ""
    return f"{base_url}/metric/summary" + (f"?{qs}" if qs else "")


def generate_datadog_metric_metadata_url(
    dd_config: DatadogMetricsConfig,
    metric_name: str,
) -> str:
    base_url = convert_api_url_to_app_url(dd_config.api_url)
    params = {"metric": metric_name}
    return f"{base_url}/metric/summary?{urlencode(params)}"


def generate_datadog_metric_tags_url(
    dd_config: DatadogMetricsConfig,
    metric_name: str,
) -> str:
    base_url = convert_api_url_to_app_url(dd_config.api_url)
    params = {"metric": metric_name}
    return f"{base_url}/metric/summary?{urlencode(params)}"


def generate_datadog_spans_url(
    dd_config: DatadogTracesConfig,
    query: str,
    from_time_ms: int,
    to_time_ms: int,
) -> str:
    base_url = convert_api_url_to_app_url(dd_config.api_url)

    url_params = {
        "query": query,
        "from_ts": from_time_ms,
        "to_ts": to_time_ms,
        "live": "true",
    }

    return f"{base_url}/apm/traces?{urlencode(url_params)}"


def generate_datadog_spans_analytics_url(
    dd_config: DatadogTracesConfig,
    query: str,
    from_time_ms: int,
    to_time_ms: int,
) -> str:
    base_url = convert_api_url_to_app_url(dd_config.api_url)

    url_params = {
        "query": query,
        "from_ts": from_time_ms,
        "to_ts": to_time_ms,
        "live": "true",
    }

    return f"{base_url}/apm/analytics?{urlencode(url_params)}"


def generate_datadog_logs_url(
    dd_config: DatadogLogsConfig,
    params: dict,
) -> str:
    base_url = convert_api_url_to_app_url(dd_config.api_url)
    url_params = {
        "query": params["filter"]["query"],
        "from_ts": params["filter"]["from"],
        "to_ts": params["filter"]["to"],
        "live": "true",
        "storage": params["filter"]["storage_tier"],
    }

    if dd_config.indexes != ["*"]:
        url_params["index"] = ",".join(dd_config.indexes)

    # Construct the full URL
    return f"{base_url}/logs?{urlencode(url_params)}"


def _build_qs(
    query_params: Optional[Dict[str, Any]], allowed: Optional[set] = None
) -> str:
    if not query_params:
        return ""
    allowed = allowed or {
        "filter",
        "query",
        "tags",
        "status",
        "start",
        "end",
        "from",
        "to",
    }
    url_params = {}
    for k, v in query_params.items():
        if k not in allowed or v is None:
            continue
        if k in ("start", "from"):
            url_params["from_ts"] = v * 1000
        elif k in ("end", "to"):
            url_params["to_ts"] = v * 1000
        elif k in ("query", "filter", "tags"):
            url_params["q"] = v
        else:
            url_params[k] = v
    qs = urlencode(url_params) if url_params else ""
    return f"?{qs}" if qs else ""


def generate_datadog_general_url(
    dd_config: DatadogGeneralConfig,
    endpoint: str,
    query_params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    base_url = convert_api_url_to_app_url(dd_config.api_url)
    path = urlparse(endpoint).path

    if "/logs" in path:
        return f"{base_url}/logs{_build_qs(query_params, {'start', 'end'})}"

    if "/monitor" in path:
        qs = _build_qs(query_params, {"filter", "query", "tags", "status"})
        monitor_id_match = re.search(r"/monitor/(\d+)", path)
        if monitor_id_match:
            return f"{base_url}/monitors/{monitor_id_match.group(1)}{qs}"
        return f"{base_url}/monitors{qs}"

    if "/dashboard" in path:
        qs = _build_qs(query_params, {"filter", "query", "tags"})
        if re.match(r"^/api/v\d+/dashboard/[^/]+", path):
            return f"{base_url}/dashboard/{path.split('/')[-1]}{qs}"
        return f"{base_url}/dashboard{qs}"

    if "/slo" in path:
        qs = _build_qs(query_params, {"filter", "query", "tags"})
        if re.match(r"^/api/v\d+/slo/[^/]+", path):
            return f"{base_url}/slo/{path.split('/')[-1]}{qs}"
        return f"{base_url}/slo{qs}"

    if "/events" in path:
        return f"{base_url}/events{_build_qs(query_params, {'start', 'end'})}"

    if "/incidents" in path:
        qs = _build_qs(query_params, {"filter", "query", "status"})
        if re.match(r"^/api/v\d+/incidents/[^/]+", path):
            return f"{base_url}/incidents/{path.split('/')[-1]}{qs}"
        return f"{base_url}/incidents{qs}"

    if "/synthetics" in path:
        qs = _build_qs(query_params, {"filter", "query", "tags", "status"})
        if re.match(r"^/api/v\d+/synthetics/tests/[^/]+", path):
            return f"{base_url}/synthetics/tests/{path.split('/')[-1]}{qs}"
        return f"{base_url}/synthetics/tests{qs}"

    if "/hosts" in path:
        return f"{base_url}/infrastructure{_build_qs(query_params, {'filter', 'query', 'tags'})}"

    if "/services" in path:
        return f"{base_url}/apm/services{_build_qs(query_params, {'filter', 'query', 'tags'})}"

    if "/metrics" in path or "/query" in path:
        return f"{base_url}/metrics/explorer{_build_qs(query_params, {'from', 'to', 'query'})}"

    return f"{base_url}/apm/home"
