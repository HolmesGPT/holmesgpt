"""
MCP server mimicking a large real-world streaming analytics platform with ~90
tools that have overlapping but subtly different parameter structures.

Uses the LOW-LEVEL mcp.server.Server API (not FastMCP) so we can inspect
raw argument types BEFORE any auto-parsing. This lets us detect when the LLM
sends stringified JSON (e.g. time_range='{"minutes":15}') vs proper objects
(time_range={"minutes":15}).

On old HolmesGPT code (before schema resolution), $ref and anyOf in tool
schemas degrade to type="string", causing the LLM to send strings. The
coercion fix converts them back, but without it, this server returns a WRONG
verification code.

Combines all hard MCP schema patterns into a single eval:
1. additionalProperties with Union types (Dict[str, Union[str, List[str]]])
2. Cross-tool parameter confusion (metrics vs selected_metrics, filters vs filter)
3. Nested $ref schemas (TimeRange, HistoricalTimeRange)
4. ~88 tools including 60 noise tools from unrelated domains
5. Wrong verification code when params arrive as strings instead of objects
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERIFICATION_CODE_CORRECT = "TS-EVAL-8k3m5n"
VERIFICATION_CODE_WRONG = "TS-EVAL-WRONG-0z0z0z"

# Data keyed by (device_name, country_code)
TIMESERIES_DATA: Dict[str, Dict[tuple, list]] = {
    "quality_index": {
        ("smart_tv", "FR"): [
            {"timestamp": "2026-03-15T21:10:00Z", "value": 87.3},
            {"timestamp": "2026-03-15T21:11:00Z", "value": 86.9},
            {"timestamp": "2026-03-15T21:12:00Z", "value": 88.1},
        ],
        ("tablet", "FR"): [
            {"timestamp": "2026-03-15T21:10:00Z", "value": 79.4},
            {"timestamp": "2026-03-15T21:11:00Z", "value": 80.2},
            {"timestamp": "2026-03-15T21:12:00Z", "value": 78.8},
        ],
        ("mobile", "FR"): [
            {"timestamp": "2026-03-15T21:10:00Z", "value": 72.1},
            {"timestamp": "2026-03-15T21:11:00Z", "value": 73.5},
            {"timestamp": "2026-03-15T21:12:00Z", "value": 71.8},
        ],
        ("smart_tv", "US"): [
            {"timestamp": "2026-03-15T21:10:00Z", "value": 91.2},
            {"timestamp": "2026-03-15T21:11:00Z", "value": 90.8},
            {"timestamp": "2026-03-15T21:12:00Z", "value": 92.0},
        ],
        ("desktop", "DE"): [
            {"timestamp": "2026-03-15T21:10:00Z", "value": 85.0},
            {"timestamp": "2026-03-15T21:11:00Z", "value": 84.5},
            {"timestamp": "2026-03-15T21:12:00Z", "value": 85.3},
        ],
    },
}

# Aggregate data returned when no filters (all combos averaged)
UNFILTERED_DATA = [
    {"timestamp": "2026-03-15T21:10:00Z", "value": 83.4},
    {"timestamp": "2026-03-15T21:11:00Z", "value": 83.2},
    {"timestamp": "2026-03-15T21:12:00Z", "value": 83.7},
]

# Granularity enums
REALTIME_GRANULARITY = ["ALL", "PT1M"] + [f"PT{s}S" for s in range(10, 60)]
NETWORK_REALTIME_GRANULARITY = ["ALL", "PT10S", "PT1M"]
RELATIVE_TIME_ENUM = [f"PT{m}M" for m in range(1, 16)]

REALTIME_GRANULARITY_DESC = ", ".join(REALTIME_GRANULARITY)


# ---------------------------------------------------------------------------
# Schema definitions (matching what FastMCP would generate from pydantic)
# ---------------------------------------------------------------------------

# TimeRange schema with $ref pattern
TIMERANGE_DEF = {
    "TimeRange": {
        "description": (
            "Real-time time range for content quality tools. Provide EITHER "
            "`minutes` (1-15) OR `start_date`/`end_date` as ISO 8601, "
            "OR `start_epoch_ms`/`end_epoch_ms` as ms epochs."
        ),
        "properties": {
            "start_date": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Start Date",
            },
            "end_date": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "End Date",
            },
            "minutes": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "default": None,
                "title": "Minutes",
            },
            "start_epoch_ms": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "default": None,
                "title": "Start Epoch Ms",
            },
            "end_epoch_ms": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "default": None,
                "title": "End Epoch Ms",
            },
        },
        "title": "TimeRange",
        "type": "object",
    }
}

HISTORICAL_TIMERANGE_DEF = {
    "HistoricalTimeRange": {
        "description": (
            "Historical time range. NOTE: No `minutes` field. "
            "Use start_date/end_date or start_epoch_ms/end_epoch_ms."
        ),
        "properties": {
            "start_date": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
            },
            "end_date": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
            },
            "start_epoch_ms": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "default": None,
            },
            "end_epoch_ms": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "default": None,
            },
        },
        "title": "HistoricalTimeRange",
        "type": "object",
    }
}

# Filters schema: additionalProperties with anyOf (Union[str, List[str]])
FILTERS_SCHEMA = {
    "anyOf": [
        {
            "additionalProperties": {
                "anyOf": [
                    {"type": "string"},
                    {"items": {"type": "string"}, "type": "array"},
                ]
            },
            "type": "object",
        },
        {"type": "null"},
    ],
    "default": None,
    "title": "Filters",
}

NULLABLE_STRING = {
    "anyOf": [{"type": "string"}, {"type": "null"}],
    "default": None,
}

NULLABLE_INT = {
    "anyOf": [{"type": "integer"}, {"type": "null"}],
    "default": None,
}


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

def _content_realtime_timeseries_schema() -> dict:
    return {
        "$defs": {**TIMERANGE_DEF},
        "properties": {
            "metrics": {
                "items": {"type": "string"},
                "title": "Metrics",
                "type": "array",
            },
            "time_range": {"$ref": "#/$defs/TimeRange"},
            "granularity": {**NULLABLE_STRING, "title": "Granularity"},
            "filters": {**FILTERS_SCHEMA},
            "benchmark_id": {**NULLABLE_INT, "title": "Benchmark Id"},
            "account_name": {**NULLABLE_STRING, "title": "Account Name"},
        },
        "required": ["metrics", "time_range"],
        "title": "get_content_realtime_timeseriesArguments",
        "type": "object",
    }


def _content_realtime_group_by_schema() -> dict:
    return {
        "$defs": {**TIMERANGE_DEF},
        "properties": {
            "metrics": {"items": {"type": "string"}, "type": "array"},
            "dimension": {"type": "string"},
            "time_range": {"$ref": "#/$defs/TimeRange"},
            "granularity": {**NULLABLE_STRING},
            "filters": {**FILTERS_SCHEMA},
            "benchmark_id": {**NULLABLE_INT},
            "limit": {**NULLABLE_INT},
            "sort_by": {**NULLABLE_STRING},
            "sort_order": {**NULLABLE_STRING},
        },
        "required": ["metrics", "dimension", "time_range"],
        "type": "object",
    }


def _content_historical_timeseries_schema() -> dict:
    return {
        "$defs": {**TIMERANGE_DEF},
        "properties": {
            "metrics": {"items": {"type": "string"}, "type": "array"},
            "time_range": {"$ref": "#/$defs/TimeRange"},
            "granularity": {**NULLABLE_STRING},
            "filters": {**FILTERS_SCHEMA},
            "benchmark_id": {**NULLABLE_INT},
        },
        "required": ["metrics", "time_range"],
        "type": "object",
    }


def _content_historical_group_by_schema() -> dict:
    return {
        "$defs": {**TIMERANGE_DEF},
        "properties": {
            "metrics": {"items": {"type": "string"}, "type": "array"},
            "dimension": {"type": "string"},
            "time_range": {"$ref": "#/$defs/TimeRange"},
            "granularity": {**NULLABLE_STRING},
            "filters": {**FILTERS_SCHEMA},
            "benchmark_id": {**NULLABLE_INT},
            "limit": {**NULLABLE_INT},
        },
        "required": ["metrics", "dimension", "time_range"],
        "type": "object",
    }


def _network_realtime_schema(extra_props: Optional[dict] = None) -> dict:
    props = {
        "selected_metrics": {"type": "string", "title": "Selected Metrics"},
        "relative_time_interval": {"type": "string"},
        "granularity": {**NULLABLE_STRING},
        "filter": {**NULLABLE_STRING, "title": "Filter"},
    }
    required = ["selected_metrics", "relative_time_interval"]
    if extra_props:
        props.update(extra_props)
        required.extend(k for k in extra_props if not extra_props[k].get("default"))
    return {"properties": props, "required": required, "type": "object"}


def _network_historical_schema(extra_props: Optional[dict] = None) -> dict:
    props = {
        "selected_metrics": {"type": "string"},
        "time_range": {"$ref": "#/$defs/HistoricalTimeRange"},
        "granularity": {**NULLABLE_STRING},
        "filter": {**NULLABLE_STRING},
    }
    required = ["selected_metrics", "time_range"]
    if extra_props:
        props.update(extra_props)
        required.extend(k for k in extra_props if not extra_props[k].get("default"))
    schema = {"properties": props, "required": required, "type": "object"}
    schema["$defs"] = {**HISTORICAL_TIMERANGE_DEF}
    return schema


def _simple_schema(props: dict, required: Optional[List[str]] = None) -> dict:
    return {
        "properties": props,
        "required": required or list(props.keys()),
        "type": "object",
    }


# ---------------------------------------------------------------------------
# Helper: validate raw arguments for the target tool
# ---------------------------------------------------------------------------

def _params_have_correct_types(arguments: dict) -> bool:
    """Check that time_range and filters arrived as objects, not strings."""
    tr = arguments.get("time_range")
    if tr is not None and not isinstance(tr, dict):
        return False

    f = arguments.get("filters")
    if f is not None and not isinstance(f, dict):
        return False

    m = arguments.get("metrics")
    if m is not None and not isinstance(m, list):
        return False

    return True


def _parse_if_string(val: Any) -> Any:
    """Parse JSON string to object if needed, for data extraction."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _filter_timeseries(metric_data: dict, filters: Optional[dict]) -> list:
    """Filter timeseries data by device_name and geo_country_code."""
    if not filters or not isinstance(filters, dict):
        return UNFILTERED_DATA

    device_filter = filters.get("device_name")
    country_filter = filters.get("geo_country_code")

    devices = [device_filter] if isinstance(device_filter, str) else (device_filter or [])
    countries = [country_filter] if isinstance(country_filter, str) else (country_filter or [])

    results = []
    seen = set()
    for (device, country), points in metric_data.items():
        if devices and device not in devices:
            continue
        if countries and country not in countries:
            continue
        for pt in points:
            key = (device, country, pt["timestamp"])
            if key not in seen:
                seen.add(key)
                results.append({
                    "timestamp": pt["timestamp"],
                    "value": pt["value"],
                    "device_name": device,
                    "geo_country_code": country,
                })
    results.sort(key=lambda x: (x["device_name"], x["timestamp"]))
    return results


# ---------------------------------------------------------------------------
# Tool handler implementations
# ---------------------------------------------------------------------------

def _handle_content_realtime_timeseries(arguments: dict) -> str:
    # Check RAW types — this is the whole point of using low-level Server API
    types_ok = _params_have_correct_types(arguments)
    verification = VERIFICATION_CODE_CORRECT if types_ok else VERIFICATION_CODE_WRONG

    # Parse stringified params for data extraction (so we still return data)
    metrics = _parse_if_string(arguments.get("metrics", []))
    time_range = _parse_if_string(arguments.get("time_range", {}))
    filters = _parse_if_string(arguments.get("filters"))

    if not isinstance(metrics, list):
        metrics = [str(metrics)]

    lines = [f"Verification: {verification}"]
    lines.append(f"Query: metrics={metrics}, time_range={time_range}")

    if not types_ok:
        lines.append(
            "WARNING: Parameters received as strings instead of objects. "
            "This indicates broken schema resolution."
        )

    if filters and isinstance(filters, dict):
        lines.append(f"Filters applied: {filters}")
    else:
        lines.append("WARNING: No valid filters - returning aggregate data")

    for metric in metrics:
        metric_data = TIMESERIES_DATA.get(metric)
        if metric_data is None:
            lines.append(f"  {metric}: no data available")
            continue
        if filters and isinstance(filters, dict):
            data = _filter_timeseries(metric_data, filters)
        else:
            data = UNFILTERED_DATA
        if not data:
            lines.append(f"  {metric}: no data matching filters")
            continue
        lines.append(f"  {metric}:")
        for pt in data:
            extra = ""
            if "device_name" in pt:
                extra = f" (device={pt['device_name']}, country={pt['geo_country_code']})"
            lines.append(f"    {pt['timestamp']}: {pt['value']}{extra}")

    return "\n".join(lines)


def _handle_content_realtime_group_by(arguments: dict) -> str:
    return "GroupBy: not available in test mode"


def _handle_metadata() -> str:
    return (
        "Available content quality metrics:\n"
        "  quality_index - Composite quality score (0-100)\n"
        "  bitrate - Average bitrate in kbps\n"
        "  error_rate - Error percentage (0-100)\n"
        "  rebuffer_ratio - Rebuffering time ratio\n"
        "  startup_time - Time to first frame (ms)\n"
        "  throughput - Network throughput in kbps\n"
        "  concurrent_viewers - Active viewer count\n"
        "  session_count - Total sessions\n"
        "  exit_before_video_start - EBVS percentage\n"
        "  video_playback_failures - VPF percentage\n"
        "  average_frame_rate - Average FPS\n"
        "  connection_induced_rebuffer - CIR percentage\n"
        "\nAvailable dimensions for filters and group_by:\n"
        "  device_name - Device model (e.g. 'smart_tv', 'mobile', 'desktop', 'tablet', 'stb')\n"
        "  geo_country_code - ISO country code (e.g. 'FR', 'US', 'DE', 'JP')\n"
        "  browser_name - Browser (e.g. 'Chrome', 'Safari', 'Firefox')\n"
        "  os_name - OS (e.g. 'Android', 'iOS', 'Windows', 'macOS')\n"
        "  cdn_provider - CDN (e.g. 'cloudfront', 'akamai', 'fastly')\n"
        "  content_type - Category (e.g. 'live', 'vod', 'linear')\n"
        "  isp - Internet service provider\n"
        "  stream_protocol - Protocol (e.g. 'HLS', 'DASH', 'CMAF')\n"
        "  player_version - Player version string\n"
        "  geo_city - City name\n"
        "  geo_region - Region/state\n"
        "  network_type - Connection type (e.g. 'wifi', 'cellular', 'wired')\n"
        "  resolution - Video resolution (e.g. '1080p', '4K', '720p')\n"
        "\nNOTE: Use exact dimension names as keys in the `filters` dict.\n"
        "  e.g. filters={\"device_name\": \"smart_tv\", \"geo_country_code\": \"FR\"}\n"
        "  Multi-select: filters={\"device_name\": [\"smart_tv\", \"tablet\"]}\n"
        f"\nRealtime granularities: {REALTIME_GRANULARITY_DESC}"
    )


def _handle_benchmarks(arguments: dict) -> str:
    return (
        "Available benchmarks:\n"
        "  1 - Global Average\n  2 - Top 10%\n  3 - Same Content Type\n"
        "  4 - Same Region\n  5 - Industry Median\n"
        "Use benchmark_id parameter in timeseries/groupby tools."
    )


def _handle_network_metadata() -> str:
    return (
        "Network analytics metrics (comma-separated in selected_metrics):\n"
        "  downstream_throughput, upstream_throughput, latency, jitter,\n"
        "  packet_loss, dns_lookup_time, tcp_connect_time, tls_handshake_time,\n"
        "  http_response_time, connection_count, bytes_transferred, retransmit_rate\n"
        "\nDimensions (use in SQL WHERE for filter param):\n"
        "  subscriber_id, device_class, access_network, cell_tower_id,\n"
        "  service_category, protocol, destination_domain, geo_region\n"
        f"\nRealtime granularities: {', '.join(NETWORK_REALTIME_GRANULARITY)}\n"
        f"Relative time intervals: {', '.join(RELATIVE_TIME_ENUM)}"
    )


def _handle_network_flow_metadata() -> str:
    return (
        "Network flow models:\n"
        "  video_delivery, cdn_routing, dns_resolution, tcp_session\n"
        "Flow metrics: flow_completion_rate, avg_flow_duration, flow_error_rate"
    )


# ---------------------------------------------------------------------------
# Build all tools
# ---------------------------------------------------------------------------

def _build_tools() -> List[Tool]:
    tools: List[Tool] = []

    # === Content Quality Metadata (2) ===
    tools.append(Tool(
        name="get_content_metrics_metadata",
        description="Get available content quality metrics, dimensions, and filter options.",
        inputSchema={"properties": {}, "type": "object"},
    ))
    tools.append(Tool(
        name="get_available_benchmarks",
        description="List available quality benchmarks for comparison.",
        inputSchema=_simple_schema(
            {"metric": {**NULLABLE_STRING, "title": "Metric"}}, required=[]
        ),
    ))

    # === Content Quality Realtime (3) ===
    tools.append(Tool(
        name="get_content_realtime_timeseries",
        description=(
            "Query real-time content quality metrics as time series data points.\n\n"
            "Args:\n"
            "  metrics: Array of metric names (e.g. ['quality_index']).\n"
            "  time_range: Time range object with 'minutes' (1-15) or "
            "'start_date'/'end_date' or 'start_epoch_ms'/'end_epoch_ms'.\n"
            "  granularity: Bucket size (ALL, PT1M, PT10S-PT59S).\n"
            "  filters: Key-value filter map. Keys=dimension names, "
            "values=string or array for multi-select.\n"
            "  benchmark_id: Optional benchmark ID.\n"
            "  account_name: Account override."
        ),
        inputSchema=_content_realtime_timeseries_schema(),
    ))
    tools.append(Tool(
        name="get_content_realtime_group_by",
        description=(
            "Query real-time content quality metrics grouped by a dimension.\n\n"
            "Args:\n  metrics: Array of metric names.\n  dimension: Dimension to group by.\n"
            "  time_range: Time range object.\n  filters: Key-value filter map."
        ),
        inputSchema=_content_realtime_group_by_schema(),
    ))

    # === Content Quality Historical (2) ===
    tools.append(Tool(
        name="get_content_historical_timeseries",
        description="Query historical content quality metrics as time series (up to 90 days).",
        inputSchema=_content_historical_timeseries_schema(),
    ))
    tools.append(Tool(
        name="get_content_historical_group_by",
        description="Query historical content quality metrics grouped by dimension.",
        inputSchema=_content_historical_group_by_schema(),
    ))

    # === Network Analytics Metadata (2) ===
    tools.append(Tool(
        name="get_network_analytics_metadata",
        description=(
            "Get network analytics metrics and dimensions.\n"
            "NOTE: Network tools use DIFFERENT params than content tools:\n"
            "  - selected_metrics: COMMA-SEPARATED STRING (not array)\n"
            "  - filter: SQL WHERE clause STRING (not dict)\n"
            "  - relative_time_interval: STRING enum (not TimeRange object)"
        ),
        inputSchema={"properties": {}, "type": "object"},
    ))
    tools.append(Tool(
        name="get_network_flow_metadata",
        description="Get network flow models and metrics.",
        inputSchema={"properties": {}, "type": "object"},
    ))

    # === Network Realtime (5) ===
    tools.append(Tool(
        name="get_network_analytics_realtime_timeseries",
        description="Query real-time network analytics as time series. Uses selected_metrics (comma-sep string), relative_time_interval (enum), filter (SQL WHERE).",
        inputSchema=_network_realtime_schema(),
    ))
    tools.append(Tool(
        name="get_network_analytics_realtime_group_by",
        description="Query real-time network analytics grouped by dimension.",
        inputSchema=_network_realtime_schema({"group_by": {"type": "string"}, "limit": {**NULLABLE_INT}}),
    ))
    tools.append(Tool(
        name="get_network_flow_realtime_timeseries",
        description="Query real-time network flow metrics as time series.",
        inputSchema=_network_realtime_schema({"flow_model": {"type": "string"}}),
    ))
    tools.append(Tool(
        name="get_network_flow_realtime_group_by",
        description="Query real-time network flow metrics grouped by dimension.",
        inputSchema=_network_realtime_schema({"flow_model": {"type": "string"}, "group_by": {"type": "string"}}),
    ))
    tools.append(Tool(
        name="get_network_analytics_realtime_top_n",
        description="Get top N dimension values by network metric.",
        inputSchema=_network_realtime_schema({"group_by": {"type": "string"}, "n": {"type": "integer", "default": 10}}),
    ))

    # === Network Historical (5) ===
    tools.append(Tool(
        name="get_network_analytics_historical_timeseries",
        description="Query historical network analytics. Uses HistoricalTimeRange (no minutes field).",
        inputSchema=_network_historical_schema(),
    ))
    tools.append(Tool(
        name="get_network_analytics_historical_group_by",
        description="Query historical network analytics grouped by dimension.",
        inputSchema=_network_historical_schema({"group_by": {"type": "string"}, "limit": {**NULLABLE_INT}}),
    ))
    tools.append(Tool(
        name="get_network_flow_historical_timeseries",
        description="Query historical network flow metrics.",
        inputSchema=_network_historical_schema({"flow_model": {"type": "string"}}),
    ))
    tools.append(Tool(
        name="get_network_flow_historical_group_by",
        description="Query historical network flow metrics grouped by dimension.",
        inputSchema=_network_historical_schema({"flow_model": {"type": "string"}, "group_by": {"type": "string"}}),
    ))
    tools.append(Tool(
        name="get_network_analytics_historical_comparison",
        description="Compare network analytics across two time ranges.",
        inputSchema={
            "$defs": {**HISTORICAL_TIMERANGE_DEF},
            "properties": {
                "selected_metrics": {"type": "string"},
                "time_range": {"$ref": "#/$defs/HistoricalTimeRange"},
                "compare_time_range": {"$ref": "#/$defs/HistoricalTimeRange"},
                "filter": {**NULLABLE_STRING},
            },
            "required": ["selected_metrics", "time_range", "compare_time_range"],
            "type": "object",
        },
    ))

    # === Alerts (7) ===
    for name, desc in [
        ("list_content_quality_alerts", "List active content quality alerts."),
        ("list_ad_quality_alerts", "List active ad quality alerts."),
        ("get_content_alert_details", "Get details of a content quality alert."),
        ("get_ad_alert_details", "Get details of an ad quality alert."),
        ("get_network_alerts_summary", "Get summary of active network alerts."),
        ("get_network_alert_diagnostics", "Get diagnostic details for a network alert."),
        ("get_network_alert_severity_events", "Get severity events for a network alert."),
    ]:
        tools.append(Tool(name=name, description=desc, inputSchema={
            "properties": {"id": {**NULLABLE_STRING}},
            "type": "object",
        }))

    # === Session (3) ===
    tools.append(Tool(
        name="get_authorized_accounts",
        description="List accounts the current user has access to.",
        inputSchema={"properties": {}, "type": "object"},
    ))
    tools.append(Tool(
        name="list_viewer_sessions",
        description="List individual viewer sessions with quality data.",
        inputSchema={
            "$defs": {**TIMERANGE_DEF},
            "properties": {
                "time_range": {"$ref": "#/$defs/TimeRange"},
                "filters": {**FILTERS_SCHEMA},
                "limit": {**NULLABLE_INT},
                "sort_by": {**NULLABLE_STRING},
            },
            "required": ["time_range"],
            "type": "object",
        },
    ))
    tools.append(Tool(
        name="get_viewer_summary",
        description="Get detailed summary for a specific viewer session.",
        inputSchema=_simple_schema({"session_id": {"type": "string"}}),
    ))

    # === Noise: Incident Management (10) ===
    _noise_names = [
        ("search_incidents", "Search incidents by keyword."),
        ("get_incident_details", "Get full details of an incident."),
        ("get_incident_timeline", "Get timeline of events for an incident."),
        ("get_incident_metrics", "Get metrics associated with an incident."),
        ("list_on_call_schedules", "List on-call schedules."),
        ("get_runbook", "Retrieve a runbook by ID."),
        ("search_runbooks", "Search runbooks by keyword or tags."),
        ("create_incident_note", "Add a note to an incident."),
        ("acknowledge_incident", "Acknowledge an incident."),
        ("get_postmortem", "Get postmortem report for an incident."),
        # Source Control (10)
        ("search_repositories", "Search code repositories."),
        ("get_repository_details", "Get repository details."),
        ("list_pull_requests_scm", "List pull requests."),
        ("get_pull_request_scm", "Get pull request details."),
        ("list_commits_scm", "List recent commits."),
        ("get_commit_details", "Get commit details."),
        ("search_code_scm", "Search code across repositories."),
        ("list_branches_scm", "List branches."),
        ("get_file_contents_scm", "Get file contents from repository."),
        ("compare_branches", "Compare two branches."),
        # CI/CD (10)
        ("list_pipelines", "List CI/CD pipelines."),
        ("get_pipeline_run", "Get pipeline run details."),
        ("get_pipeline_logs", "Get logs from a pipeline run."),
        ("list_deployments", "List recent deployments."),
        ("get_deployment_details", "Get deployment details."),
        ("trigger_pipeline_run", "Trigger a pipeline run."),
        ("list_environments", "List deployment environments."),
        ("get_environment_health", "Get environment health status."),
        ("list_build_artifacts", "List pipeline build artifacts."),
        ("rollback_deployment", "Rollback a deployment."),
        # Error Tracking (10)
        ("list_error_groups", "List error groups for a project."),
        ("get_error_group_details", "Get error group details."),
        ("get_error_events", "Get individual error events."),
        ("get_error_tag_values", "Get tag value distribution for errors."),
        ("search_errors", "Search errors across projects."),
        ("list_error_releases", "List releases with error stats."),
        ("get_error_trends", "Get error count trends."),
        ("analyze_error_with_ai", "Get AI analysis of an error group."),
        ("update_error_status", "Update error group status."),
        ("list_error_projects", "List error tracking projects."),
        # Cloud Infrastructure (10)
        ("list_compute_instances", "List cloud compute instances."),
        ("get_instance_details", "Get compute instance details."),
        ("list_containers", "List containers across clusters."),
        ("get_container_logs_cloud", "Get logs from a cloud container."),
        ("list_cloud_services", "List managed cloud services."),
        ("get_service_health", "Get cloud service health."),
        ("list_load_balancers", "List load balancers."),
        ("list_storage_buckets", "List cloud storage buckets."),
        ("get_bucket_metadata_cloud", "Get storage bucket metadata."),
        ("execute_cloud_query", "Execute a cloud resource query."),
        # Workflow Orchestration (10)
        ("list_orchestration_flows", "List workflow flows."),
        ("get_orchestration_flow", "Get flow details."),
        ("list_flow_runs", "List runs of a flow."),
        ("get_flow_run_details", "Get flow run details."),
        ("get_flow_run_logs", "Get logs from a flow run."),
        ("list_task_runs", "List task runs in a flow run."),
        ("get_task_run_details", "Get task run details."),
        ("list_work_pools", "List worker pools."),
        ("search_orchestration_events", "Search orchestration events."),
        ("list_automations", "List automation rules."),
    ]
    for name, desc in _noise_names:
        tools.append(Tool(name=name, description=desc, inputSchema={
            "properties": {"query": {**NULLABLE_STRING}},
            "type": "object",
        }))

    return tools


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

ALL_TOOLS = _build_tools()
TOOL_MAP = {t.name: t for t in ALL_TOOLS}

server = Server("streaming-analytics")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return ALL_TOOLS


@server.call_tool(validate_input=False)
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    arguments = arguments or {}

    # Route to handlers
    if name == "get_content_metrics_metadata":
        text = _handle_metadata()
    elif name == "get_available_benchmarks":
        text = _handle_benchmarks(arguments)
    elif name == "get_content_realtime_timeseries":
        text = _handle_content_realtime_timeseries(arguments)
    elif name == "get_content_realtime_group_by":
        text = _handle_content_realtime_group_by(arguments)
    elif name == "get_network_analytics_metadata":
        text = _handle_network_metadata()
    elif name == "get_network_flow_metadata":
        text = _handle_network_flow_metadata()
    elif name == "get_authorized_accounts":
        text = "Accounts: demo-account-1 (Demo Streaming), demo-account-2 (Test Network)"
    elif name in TOOL_MAP:
        text = "Not available in test mode"
    else:
        text = f"Unknown tool: {name}"

    return [TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
