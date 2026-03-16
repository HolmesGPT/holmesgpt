"""
MCP server mimicking a large real-world streaming analytics platform with ~90
tools that have overlapping but subtly different parameter structures.

Combines all hard MCP schema patterns into a single eval:

1. additionalProperties with Union types (Dict[str, Union[str, List[str]]])
   on ALL content quality tools — the exact pattern that breaks without
   proper schema resolution
2. Cross-tool parameter confusion:
   - `metrics` (List[str]) vs `selected_metrics` (comma-separated str)
   - `time_range` (TimeRange object with minutes) vs
     `relative_time_interval` (string enum)
   - `filters` (Dict[str, Union[str, List[str]]]) vs `filter` (SQL WHERE)
   - `granularity` with 50+ values vs `granularity` with only 3 values
3. Nested $ref schemas (TimeRange, HistoricalTimeRange, AlertThreshold, etc.)
4. ~88 total tools including 60 noise tools from unrelated domains
5. Wrong verification code returned when filters are malformed or missing,
   ensuring the LLM cannot self-correct after a failed call without detection

The test verifies the LLM can correctly use get_content_realtime_timeseries
with the right parameter types and not confuse them with network analytics
parameters.
"""

from typing import Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("streaming-analytics")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TimeRange(BaseModel):
    """Real-time time range for content quality tools. Provide EITHER
    `minutes` (1-15) OR `start_date`/`end_date` as ISO 8601 timestamps,
    OR `start_epoch_ms`/`end_epoch_ms` as millisecond epochs."""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    minutes: Optional[int] = None
    start_epoch_ms: Optional[float] = None
    end_epoch_ms: Optional[float] = None


class HistoricalTimeRange(BaseModel):
    """Historical time range for network analytics historical tools.
    NOTE: Does NOT have a `minutes` field. Use `start_date`/`end_date`
    or `start_epoch_ms`/`end_epoch_ms`."""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_epoch_ms: Optional[float] = None
    end_epoch_ms: Optional[float] = None


class AlertThreshold(BaseModel):
    """Alert threshold configuration."""
    metric: str
    operator: str
    value: float


class IncidentFilter(BaseModel):
    """Filter for incident searches."""
    status: Optional[str] = None
    severity: Optional[str] = None
    assigned_to: Optional[str] = None
    created_after: Optional[str] = None
    created_before: Optional[str] = None


class DeploymentFilter(BaseModel):
    """Filter for deployment queries."""
    environment: Optional[str] = None
    service: Optional[str] = None
    status: Optional[str] = None
    since: Optional[str] = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERIFICATION_CODE_CORRECT = "TS-EVAL-8k3m5n"
VERIFICATION_CODE_WRONG = "TS-EVAL-WRONG-0z0z0z"
VERIFICATION_CODE_GRP = "GRP-EVAL-5w9t1q"
VERIFICATION_CODE_NET = "NET-EVAL-4p2x7r"
VERIFICATION_CODE_HIST = "HIST-EVAL-3n6v8k"

TIMESERIES_DATA = {
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
    "bitrate": {
        ("smart_tv", "FR"): [
            {"timestamp": "2026-03-15T21:10:00Z", "value": 4500.2},
            {"timestamp": "2026-03-15T21:11:00Z", "value": 4480.7},
            {"timestamp": "2026-03-15T21:12:00Z", "value": 4520.1},
        ],
    },
    "error_rate": {
        ("smart_tv", "FR"): [
            {"timestamp": "2026-03-15T21:10:00Z", "value": 0.8},
            {"timestamp": "2026-03-15T21:11:00Z", "value": 0.9},
            {"timestamp": "2026-03-15T21:12:00Z", "value": 0.7},
        ],
    },
}

# Unfiltered data returned when no filters provided (includes ALL combos)
TIMESERIES_UNFILTERED = {
    "quality_index": [
        {"timestamp": "2026-03-15T21:10:00Z", "value": 83.4},
        {"timestamp": "2026-03-15T21:11:00Z", "value": 83.2},
        {"timestamp": "2026-03-15T21:12:00Z", "value": 83.7},
    ],
}

# Content quality realtime granularity: 50+ values
REALTIME_GRANULARITY = ["ALL", "PT1M"] + [f"PT{s}S" for s in range(10, 60)]

# Content quality historical granularity: 70+ values
HISTORICAL_GRANULARITY = (
    ["ALL"]
    + [f"PT{m}M" for m in range(1, 31)]
    + [f"PT{h}H" for h in range(1, 24)]
    + [f"P{d}D" for d in range(1, 31)]
    + [f"P{w}W" for w in range(1, 11)]
)

# Network analytics realtime granularity: only 3 values
NETWORK_REALTIME_GRANULARITY = ["ALL", "PT10S", "PT1M"]

# Network analytics historical granularity: 9 values
NETWORK_HISTORICAL_GRANULARITY = [
    "ALL", "PT1M", "PT5M", "PT15M", "PT30M", "PT1H", "PT6H", "PT12H", "P1D",
]

RELATIVE_TIME_ENUM = [f"PT{m}M" for m in range(1, 16)]

REALTIME_GRANULARITY_DESC = ", ".join(REALTIME_GRANULARITY)
HISTORICAL_GRANULARITY_DESC = ", ".join(HISTORICAL_GRANULARITY)


def _filters_are_valid(filters: Optional[Dict[str, Union[str, List[str]]]]) -> bool:
    """Check that filters were passed as a proper dict (not None, not a string)."""
    if filters is None:
        return False
    if not isinstance(filters, dict):
        return False
    if len(filters) == 0:
        return False
    # Check values are strings or lists of strings
    for v in filters.values():
        if isinstance(v, list):
            if not all(isinstance(item, str) for item in v):
                return False
        elif not isinstance(v, str):
            return False
    return True


def _filter_timeseries(
    metric_data: dict,
    filters: Optional[Dict[str, Union[str, List[str]]]],
) -> list:
    """Filter timeseries data by device_name and geo_country_code."""
    if not filters or not isinstance(filters, dict):
        # Return unfiltered aggregate data
        return TIMESERIES_UNFILTERED.get("quality_index", [])

    device_filter = filters.get("device_name")
    country_filter = filters.get("geo_country_code")

    # Normalize to lists
    devices = [device_filter] if isinstance(device_filter, str) else (device_filter or [])
    countries = [country_filter] if isinstance(country_filter, str) else (country_filter or [])

    results = []
    seen_timestamps = set()

    for (device, country), points in metric_data.items():
        if devices and device not in devices:
            continue
        if countries and country not in countries:
            continue
        for pt in points:
            key = (device, country, pt["timestamp"])
            if key not in seen_timestamps:
                seen_timestamps.add(key)
                results.append({
                    "timestamp": pt["timestamp"],
                    "value": pt["value"],
                    "device_name": device,
                    "geo_country_code": country,
                })

    results.sort(key=lambda x: (x["device_name"], x["timestamp"]))
    return results


# ============================================================
# CONTENT QUALITY METADATA (2 tools)
# ============================================================

@mcp.tool()
def get_content_metrics_metadata() -> str:
    """Get available content quality metrics, their descriptions, and valid
    dimension names for filtering and grouping.

    Returns metric names to use in the `metrics` parameter, and dimension names
    to use as keys in the `filters` parameter of content quality tools."""
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
        "  os_name - Operating system (e.g. 'Android', 'iOS', 'Windows', 'macOS')\n"
        "  cdn_provider - CDN (e.g. 'cloudfront', 'akamai', 'fastly')\n"
        "  content_type - Content category (e.g. 'live', 'vod', 'linear')\n"
        "  isp - Internet service provider\n"
        "  stream_protocol - Protocol (e.g. 'HLS', 'DASH', 'CMAF')\n"
        "  player_version - Player version string\n"
        "  geo_city - City name\n"
        "  geo_region - Region/state\n"
        "  network_type - Connection type (e.g. 'wifi', 'cellular', 'wired')\n"
        "  resolution - Video resolution (e.g. '1080p', '4K', '720p')\n"
        "  cdn_edge_server - CDN edge node identifier\n"
        "  content_title - Title of the content asset\n"
        "\nNOTE: Use exact dimension names above as keys in the `filters` dict.\n"
        "  e.g. filters={\"device_name\": \"smart_tv\", \"geo_country_code\": \"FR\"}\n"
        "  For multi-select: filters={\"device_name\": [\"smart_tv\", \"tablet\"]}\n"
        f"\nRealtime granularities: {REALTIME_GRANULARITY_DESC}\n"
        f"Historical granularities: {HISTORICAL_GRANULARITY_DESC}"
    )


@mcp.tool()
def get_available_benchmarks(
    metric: Optional[str] = None,
) -> str:
    """List available quality benchmarks for comparison.

    Args:
        metric: Optional metric name to filter benchmarks.
    """
    return (
        "Available benchmarks:\n"
        "  1 - Global Average (all accounts)\n"
        "  2 - Top 10% Performers\n"
        "  3 - Same Content Type Average\n"
        "  4 - Same Region Average\n"
        "  5 - Industry Median\n"
        "Use benchmark_id parameter in timeseries/groupby tools."
    )


# ============================================================
# CONTENT QUALITY REALTIME TOOLS (3 tools) - TimeRange object
# metrics: List[str], filters: Dict, granularity: large enum
# ============================================================

@mcp.tool()
def get_content_realtime_timeseries(
    metrics: List[str],
    time_range: TimeRange,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    benchmark_id: Optional[int] = None,
    account_name: Optional[str] = None,
) -> str:
    """Query real-time content quality metrics as time series data points.

    Args:
        metrics: Array of metric names to query (1-12). Use names from
            get_content_metrics_metadata.
        time_range: Time range object. Provide EITHER 'minutes' (int, 1-15)
            OR 'start_date'/'end_date' as ISO 8601 timestamps,
            OR 'start_epoch_ms'/'end_epoch_ms' as millisecond epochs.
        granularity: Time bucket size. One of: ALL, PT1M, PT10S through PT59S.
        filters: Key-value filter map. Keys are dimension names from metadata,
            values are strings or arrays of strings for multi-select.
            Example: {"device_name": "smart_tv", "geo_country_code": ["FR", "DE"]}
        benchmark_id: Optional benchmark to include for comparison.
        account_name: Account name override.
    """
    # Validate time_range is an object
    if not isinstance(time_range, TimeRange):
        return (
            f"Error: time_range must be a JSON object with fields "
            f"(minutes, start_date, end_date, start_epoch_ms, end_epoch_ms). "
            f"Got {type(time_range).__name__}: {time_range!r}"
        )
    if (time_range.minutes is None and time_range.start_date is None
            and time_range.start_epoch_ms is None):
        return (
            f"Error: time_range needs 'minutes' or 'start_date'/'end_date' "
            f"or 'start_epoch_ms'/'end_epoch_ms'. Got: {time_range.model_dump()}"
        )

    # Return WRONG verification code if filters are missing or malformed
    filters_ok = _filters_are_valid(filters)
    verification = VERIFICATION_CODE_CORRECT if filters_ok else VERIFICATION_CODE_WRONG

    lines = [f"Verification: {verification}"]
    lines.append(f"Query: metrics={metrics}, time_range={time_range.model_dump()}")

    if filters and isinstance(filters, dict):
        lines.append(f"Filters applied: {filters}")
    else:
        lines.append("WARNING: No filters applied - returning aggregate data across all dimensions")

    if benchmark_id:
        lines.append(f"Benchmark: {benchmark_id}")

    for metric in metrics:
        metric_data = TIMESERIES_DATA.get(metric)
        if metric_data is None:
            lines.append(f"  {metric}: no data available for this metric")
            continue

        if filters_ok:
            data = _filter_timeseries(metric_data, filters)
        else:
            data = TIMESERIES_UNFILTERED.get(metric, [
                {"timestamp": "2026-03-15T21:10:00Z", "value": 0.0},
            ])

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


@mcp.tool()
def get_content_realtime_group_by(
    metrics: List[str],
    dimension: str,
    time_range: TimeRange,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    benchmark_id: Optional[int] = None,
    limit: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> str:
    """Query real-time content quality metrics grouped by a single dimension.

    Args:
        metrics: Array of metric names to query.
        dimension: Dimension name to group by (from metadata).
        time_range: Time range object with minutes or start_date/end_date.
        granularity: Time bucket size from realtime enum.
        filters: Key-value filter map with dimension names as keys.
        benchmark_id: Optional benchmark for comparison.
        limit: Max result rows (default 50, max 500).
        sort_by: Metric name to sort by.
        sort_order: 'asc' or 'desc'.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be a JSON object, got {type(time_range).__name__}"
    lines = [f"Verification: {VERIFICATION_CODE_GRP}"]
    lines.append(f"GroupBy: dimension={dimension}, metrics={metrics}")
    if filters:
        lines.append(f"Filters: {filters}")
    return "\n".join(lines)


@mcp.tool()
def get_content_historical_timeseries(
    metrics: List[str],
    time_range: TimeRange,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    benchmark_id: Optional[int] = None,
) -> str:
    """Query historical content quality metrics as time series (up to 90 days).

    Args:
        metrics: Array of metric names.
        time_range: Time range object. For historical, use start_date/end_date
            or start_epoch_ms/end_epoch_ms. The 'minutes' field can also be
            used for short lookbacks.
        granularity: Historical granularity (PT1M through P10W).
        filters: Key-value filter map.
        benchmark_id: Optional benchmark for comparison.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be a JSON object, got {type(time_range).__name__}"
    return "Content historical timeseries not available in test mode"


@mcp.tool()
def get_content_historical_group_by(
    metrics: List[str],
    dimension: str,
    time_range: TimeRange,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    benchmark_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Query historical content quality metrics grouped by dimension.

    Args:
        metrics: Array of metric names.
        dimension: Dimension to group by.
        time_range: Time range object with start_date/end_date or epochs.
        granularity: Historical granularity.
        filters: Key-value filter map.
        benchmark_id: Optional benchmark.
        limit: Max rows.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be a JSON object, got {type(time_range).__name__}"
    return "Content historical group-by not available in test mode"


# ============================================================
# NETWORK ANALYTICS METADATA (2 tools)
# ============================================================

@mcp.tool()
def get_network_analytics_metadata() -> str:
    """Get available network analytics (DPI) metrics and dimensions.

    NOTE: Network analytics tools use DIFFERENT parameter types than content
    quality tools:
    - selected_metrics: COMMA-SEPARATED STRING (not array)
    - filter: SQL WHERE clause STRING (not dict)
    - Realtime tools use relative_time_interval STRING enum
    - Historical tools use HistoricalTimeRange object (no minutes field)
    """
    return (
        "Network analytics metrics (use as comma-separated string in selected_metrics):\n"
        "  downstream_throughput, upstream_throughput, latency, jitter,\n"
        "  packet_loss, dns_lookup_time, tcp_connect_time, tls_handshake_time,\n"
        "  http_response_time, connection_count, bytes_transferred, retransmit_rate\n"
        "\nDimensions (use in SQL WHERE for filter param):\n"
        "  subscriber_id, device_class, access_network, cell_tower_id,\n"
        "  service_category, protocol, destination_domain, geo_region,\n"
        "  time_of_day_bucket, content_category\n"
        f"\nRealtime granularities: {', '.join(NETWORK_REALTIME_GRANULARITY)}\n"
        f"Relative time intervals: {', '.join(RELATIVE_TIME_ENUM)}\n"
        f"Historical granularities: {', '.join(NETWORK_HISTORICAL_GRANULARITY)}"
    )


@mcp.tool()
def get_network_flow_metadata() -> str:
    """Get available network flow models and their specific metrics.

    Network flow tools use relative_time_interval (string) for realtime
    and HistoricalTimeRange (object) for historical queries."""
    return (
        "Network flow models:\n"
        "  video_delivery - Video stream delivery path analysis\n"
        "  cdn_routing - CDN routing and edge selection\n"
        "  dns_resolution - DNS query and resolution chain\n"
        "  tcp_session - TCP session lifecycle\n"
        "\nFlow-specific metrics:\n"
        "  flow_completion_rate, avg_flow_duration, flow_error_rate,\n"
        "  flow_throughput, flow_retransmit_count"
    )


# ============================================================
# NETWORK ANALYTICS REALTIME TOOLS (5 tools)
# selected_metrics: str (comma-sep), relative_time_interval: str,
# filter: str (SQL WHERE), granularity: small enum
# ============================================================

@mcp.tool()
def get_network_analytics_realtime_timeseries(
    selected_metrics: str,
    relative_time_interval: str,
    granularity: Optional[str] = None,
    filter: Optional[str] = None,
) -> str:
    """Query real-time network analytics metrics as time series.

    Args:
        selected_metrics: Comma-separated metric names (e.g. "latency,jitter,packet_loss").
        relative_time_interval: ISO 8601 duration from enum (PT1M through PT15M).
        granularity: One of: ALL, PT10S, PT1M.
        filter: SQL WHERE clause for filtering (e.g. "device_class = 'mobile' AND geo_region = 'NA'").
    """
    return f"Verification: {VERIFICATION_CODE_NET}\nNetwork realtime timeseries not available in test mode"


@mcp.tool()
def get_network_analytics_realtime_group_by(
    selected_metrics: str,
    group_by: str,
    relative_time_interval: str,
    granularity: Optional[str] = None,
    filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Query real-time network analytics metrics grouped by dimension.

    Args:
        selected_metrics: Comma-separated metric names.
        group_by: Dimension to group by.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        granularity: One of: ALL, PT10S, PT1M.
        filter: SQL WHERE clause.
        limit: Max rows (default 100).
    """
    return "Network realtime group-by not available in test mode"


@mcp.tool()
def get_network_flow_realtime_timeseries(
    flow_model: str,
    selected_metrics: str,
    relative_time_interval: str,
    granularity: Optional[str] = None,
    filter: Optional[str] = None,
) -> str:
    """Query real-time network flow metrics as time series.

    Args:
        flow_model: Flow model ID from get_network_flow_metadata.
        selected_metrics: Comma-separated flow metric names.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        granularity: One of: ALL, PT10S, PT1M.
        filter: SQL WHERE clause.
    """
    return "Network flow realtime timeseries not available in test mode"


@mcp.tool()
def get_network_flow_realtime_group_by(
    flow_model: str,
    selected_metrics: str,
    group_by: str,
    relative_time_interval: str,
    filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Query real-time network flow metrics grouped by dimension.

    Args:
        flow_model: Flow model ID.
        selected_metrics: Comma-separated flow metric names.
        group_by: Dimension to group by.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        filter: SQL WHERE clause.
        limit: Max rows.
    """
    return "Network flow realtime group-by not available in test mode"


@mcp.tool()
def get_network_analytics_realtime_top_n(
    selected_metrics: str,
    group_by: str,
    relative_time_interval: str,
    n: int = 10,
    filter: Optional[str] = None,
) -> str:
    """Get top N dimension values by network metric in real-time.

    Args:
        selected_metrics: Comma-separated metric names.
        group_by: Dimension to rank.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        n: Number of top values (default 10).
        filter: SQL WHERE clause.
    """
    return "Network realtime top-N not available in test mode"


# ============================================================
# NETWORK ANALYTICS HISTORICAL TOOLS (5 tools)
# Uses HistoricalTimeRange (NO minutes field!)
# ============================================================

@mcp.tool()
def get_network_analytics_historical_timeseries(
    selected_metrics: str,
    time_range: HistoricalTimeRange,
    granularity: Optional[str] = None,
    filter: Optional[str] = None,
) -> str:
    """Query historical network analytics metrics as time series.

    Args:
        selected_metrics: Comma-separated metric names.
        time_range: Historical time range with start_date/end_date or
            start_epoch_ms/end_epoch_ms. NOTE: No 'minutes' field.
        granularity: One of: ALL, PT1M, PT5M, PT15M, PT30M, PT1H, PT6H, PT12H, P1D.
        filter: SQL WHERE clause.
    """
    return "Network historical timeseries not available in test mode"


@mcp.tool()
def get_network_analytics_historical_group_by(
    selected_metrics: str,
    group_by: str,
    time_range: HistoricalTimeRange,
    granularity: Optional[str] = None,
    filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Query historical network analytics grouped by dimension.

    Args:
        selected_metrics: Comma-separated metric names.
        group_by: Dimension to group by.
        time_range: Historical time range (no minutes field).
        granularity: Historical network granularity.
        filter: SQL WHERE clause.
        limit: Max rows.
    """
    return "Network historical group-by not available in test mode"


@mcp.tool()
def get_network_flow_historical_timeseries(
    flow_model: str,
    selected_metrics: str,
    time_range: HistoricalTimeRange,
    granularity: Optional[str] = None,
    filter: Optional[str] = None,
) -> str:
    """Query historical network flow metrics as time series.

    Args:
        flow_model: Flow model ID.
        selected_metrics: Comma-separated flow metric names.
        time_range: Historical time range (no minutes field).
        granularity: Historical network granularity.
        filter: SQL WHERE clause.
    """
    return "Network flow historical timeseries not available in test mode"


@mcp.tool()
def get_network_flow_historical_group_by(
    flow_model: str,
    selected_metrics: str,
    group_by: str,
    time_range: HistoricalTimeRange,
    granularity: Optional[str] = None,
    filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Query historical network flow metrics grouped by dimension.

    Args:
        flow_model: Flow model ID.
        selected_metrics: Comma-separated flow metric names.
        group_by: Dimension to group by.
        time_range: Historical time range (no minutes field).
        granularity: Historical network granularity.
        filter: SQL WHERE clause.
        limit: Max rows.
    """
    return "Network flow historical group-by not available in test mode"


@mcp.tool()
def get_network_analytics_historical_comparison(
    selected_metrics: str,
    time_range: HistoricalTimeRange,
    compare_time_range: HistoricalTimeRange,
    filter: Optional[str] = None,
) -> str:
    """Compare network analytics across two historical time ranges.

    Args:
        selected_metrics: Comma-separated metric names.
        time_range: Primary time range.
        compare_time_range: Comparison time range.
        filter: SQL WHERE clause.
    """
    return "Network historical comparison not available in test mode"


# ============================================================
# CONTENT QUALITY ALERTS (4 tools)
# ============================================================

@mcp.tool()
def list_content_quality_alerts(
    severity: Optional[str] = None,
    status: Optional[str] = None,
    since_minutes: Optional[int] = None,
) -> str:
    """List active content quality alerts.

    Args:
        severity: Filter by severity (critical, warning, info).
        status: Filter by status (active, acknowledged, resolved).
        since_minutes: Only alerts from last N minutes.
    """
    return "No content quality alerts in test mode"


@mcp.tool()
def list_ad_quality_alerts(
    severity: Optional[str] = None,
    status: Optional[str] = None,
    since_minutes: Optional[int] = None,
) -> str:
    """List active ad quality alerts.

    Args:
        severity: Filter by severity (critical, warning, info).
        status: Filter by status (active, acknowledged, resolved).
        since_minutes: Only alerts from last N minutes.
    """
    return "No ad quality alerts in test mode"


@mcp.tool()
def get_content_alert_details(
    alert_id: str,
) -> str:
    """Get detailed information about a specific content quality alert.

    Args:
        alert_id: The alert identifier.
    """
    return "Content alert details not available in test mode"


@mcp.tool()
def get_ad_alert_details(
    alert_id: str,
) -> str:
    """Get detailed information about a specific ad quality alert.

    Args:
        alert_id: The alert identifier.
    """
    return "Ad alert details not available in test mode"


# ============================================================
# NETWORK ALERTS (3 tools)
# ============================================================

@mcp.tool()
def get_network_alerts_summary(
    since_minutes: Optional[int] = None,
    severity: Optional[str] = None,
    region: Optional[str] = None,
) -> str:
    """Get summary of active network alerts.

    Args:
        since_minutes: Look back N minutes (default 30).
        severity: Filter by severity.
        region: Filter by network region.
    """
    return "No network alerts in test mode"


@mcp.tool()
def get_network_alert_diagnostics(
    alert_id: str,
    include_packet_capture: bool = False,
) -> str:
    """Get diagnostic details for a network alert.

    Args:
        alert_id: The network alert identifier.
        include_packet_capture: Include packet capture summary.
    """
    return "Network alert diagnostics not available in test mode"


@mcp.tool()
def get_network_alert_severity_events(
    alert_id: str,
    limit: Optional[int] = None,
) -> str:
    """Get severity change events for a network alert.

    Args:
        alert_id: The network alert identifier.
        limit: Max events to return (default 50).
    """
    return "Network alert severity events not available in test mode"


# ============================================================
# SESSION TOOLS (3 tools)
# ============================================================

@mcp.tool()
def get_authorized_accounts() -> str:
    """List accounts the current user has access to."""
    return "Accounts: demo-account-1 (Demo Streaming), demo-account-2 (Test Network)"


@mcp.tool()
def list_viewer_sessions(
    time_range: TimeRange,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    limit: Optional[int] = None,
    sort_by: Optional[str] = None,
) -> str:
    """List individual viewer sessions with quality data.

    Args:
        time_range: Time range object with minutes or start_date/end_date.
        filters: Key-value filter map with dimension names.
        limit: Max sessions (default 20, max 200).
        sort_by: Sort field (session_start, quality_index, error_count).
    """
    return "Viewer sessions not available in test mode"


@mcp.tool()
def get_viewer_summary(
    session_id: str,
) -> str:
    """Get detailed summary for a specific viewer session.

    Args:
        session_id: The session identifier.
    """
    return "Viewer summary not available in test mode"


# ============================================================
# NOISE TOOLS: Incident Management (10 tools)
# ============================================================

@mcp.tool()
def search_incidents(
    query: str,
    filters: Optional[IncidentFilter] = None,
    limit: int = 25,
) -> str:
    """Search incidents by keyword or filter criteria.

    Args:
        query: Search query string.
        filters: Structured filter with status, severity, assigned_to, date range.
        limit: Max results (default 25).
    """
    return "Not available in test mode"


@mcp.tool()
def get_incident_details(incident_id: str) -> str:
    """Get full details of a specific incident.

    Args:
        incident_id: The incident identifier (e.g. INC-12345).
    """
    return "Not available in test mode"


@mcp.tool()
def get_incident_timeline(incident_id: str, limit: int = 50) -> str:
    """Get chronological timeline of events for an incident.

    Args:
        incident_id: The incident identifier.
        limit: Max timeline entries.
    """
    return "Not available in test mode"


@mcp.tool()
def get_incident_metrics(
    incident_id: str,
    metric_names: Optional[List[str]] = None,
) -> str:
    """Get metrics associated with an incident.

    Args:
        incident_id: The incident identifier.
        metric_names: Optional list of specific metrics to retrieve.
    """
    return "Not available in test mode"


@mcp.tool()
def list_on_call_schedules(
    team: Optional[str] = None,
    date: Optional[str] = None,
) -> str:
    """List on-call schedules.

    Args:
        team: Filter by team name.
        date: Specific date (ISO 8601).
    """
    return "Not available in test mode"


@mcp.tool()
def get_runbook(runbook_id: str) -> str:
    """Retrieve a specific runbook by ID.

    Args:
        runbook_id: The runbook identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def search_runbooks(
    query: str,
    tags: Optional[List[str]] = None,
    limit: int = 10,
) -> str:
    """Search runbooks by keyword or tags.

    Args:
        query: Search query.
        tags: Filter by tags.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def create_incident_note(
    incident_id: str,
    note: str,
    visibility: str = "internal",
) -> str:
    """Add a note to an incident (read-only in test mode).

    Args:
        incident_id: The incident identifier.
        note: Note content.
        visibility: 'internal' or 'external'.
    """
    return "Not available in test mode"


@mcp.tool()
def acknowledge_incident(
    incident_id: str,
    message: Optional[str] = None,
) -> str:
    """Acknowledge an incident (read-only in test mode).

    Args:
        incident_id: The incident identifier.
        message: Optional acknowledgement message.
    """
    return "Not available in test mode"


@mcp.tool()
def get_postmortem(incident_id: str) -> str:
    """Get the postmortem report for a resolved incident.

    Args:
        incident_id: The incident identifier.
    """
    return "Not available in test mode"


# ============================================================
# NOISE TOOLS: Source Control (10 tools)
# ============================================================

@mcp.tool()
def search_repositories(
    query: str,
    language: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Search code repositories.

    Args:
        query: Search query.
        language: Filter by programming language.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_repository_details(repo_slug: str) -> str:
    """Get details of a specific repository.

    Args:
        repo_slug: Repository identifier (e.g. org/repo-name).
    """
    return "Not available in test mode"


@mcp.tool()
def list_pull_requests_scm(
    repo_slug: str,
    state: str = "open",
    limit: int = 25,
) -> str:
    """List pull requests for a repository.

    Args:
        repo_slug: Repository identifier.
        state: PR state (open, closed, merged).
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_pull_request_scm(
    repo_slug: str,
    pr_number: int,
) -> str:
    """Get details of a specific pull request.

    Args:
        repo_slug: Repository identifier.
        pr_number: Pull request number.
    """
    return "Not available in test mode"


@mcp.tool()
def list_commits_scm(
    repo_slug: str,
    branch: str = "main",
    since: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List recent commits on a branch.

    Args:
        repo_slug: Repository identifier.
        branch: Branch name.
        since: Only commits after this ISO 8601 date.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_commit_details(
    repo_slug: str,
    commit_sha: str,
) -> str:
    """Get details of a specific commit.

    Args:
        repo_slug: Repository identifier.
        commit_sha: Commit SHA hash.
    """
    return "Not available in test mode"


@mcp.tool()
def search_code_scm(
    query: str,
    repo_slug: Optional[str] = None,
    file_extension: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Search code across repositories.

    Args:
        query: Code search query.
        repo_slug: Optional repo to limit search.
        file_extension: Filter by file extension.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def list_branches_scm(
    repo_slug: str,
    limit: int = 50,
) -> str:
    """List branches in a repository.

    Args:
        repo_slug: Repository identifier.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_file_contents_scm(
    repo_slug: str,
    file_path: str,
    ref: str = "main",
) -> str:
    """Get contents of a file from a repository.

    Args:
        repo_slug: Repository identifier.
        file_path: Path to file in repository.
        ref: Branch, tag, or commit SHA.
    """
    return "Not available in test mode"


@mcp.tool()
def compare_branches(
    repo_slug: str,
    base: str,
    head: str,
) -> str:
    """Compare two branches and show diff summary.

    Args:
        repo_slug: Repository identifier.
        base: Base branch name.
        head: Head branch name.
    """
    return "Not available in test mode"


# ============================================================
# NOISE TOOLS: CI/CD (10 tools)
# ============================================================

@mcp.tool()
def list_pipelines(
    project: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List CI/CD pipelines.

    Args:
        project: Filter by project name.
        status: Filter by status (running, success, failed, pending).
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_pipeline_run(
    pipeline_id: str,
    run_id: str,
) -> str:
    """Get details of a specific pipeline run.

    Args:
        pipeline_id: Pipeline identifier.
        run_id: Run identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def get_pipeline_logs(
    pipeline_id: str,
    run_id: str,
    step: Optional[str] = None,
    tail: int = 100,
) -> str:
    """Get logs from a pipeline run.

    Args:
        pipeline_id: Pipeline identifier.
        run_id: Run identifier.
        step: Specific step name (optional).
        tail: Number of log lines from end.
    """
    return "Not available in test mode"


@mcp.tool()
def list_deployments(
    filters: Optional[DeploymentFilter] = None,
    limit: int = 20,
) -> str:
    """List recent deployments.

    Args:
        filters: Structured deployment filter.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_deployment_details(deployment_id: str) -> str:
    """Get details of a specific deployment.

    Args:
        deployment_id: Deployment identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def trigger_pipeline_run(
    pipeline_id: str,
    branch: str = "main",
    parameters: Optional[Dict[str, str]] = None,
) -> str:
    """Trigger a pipeline run (read-only in test mode).

    Args:
        pipeline_id: Pipeline identifier.
        branch: Branch to build.
        parameters: Key-value build parameters.
    """
    return "Not available in test mode"


@mcp.tool()
def list_environments(
    project: Optional[str] = None,
) -> str:
    """List deployment environments.

    Args:
        project: Filter by project name.
    """
    return "Not available in test mode"


@mcp.tool()
def get_environment_health(
    environment_id: str,
) -> str:
    """Get health status of a deployment environment.

    Args:
        environment_id: Environment identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def list_build_artifacts(
    pipeline_id: str,
    run_id: str,
) -> str:
    """List artifacts produced by a pipeline run.

    Args:
        pipeline_id: Pipeline identifier.
        run_id: Run identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def rollback_deployment(
    deployment_id: str,
    target_version: str,
) -> str:
    """Rollback a deployment to a previous version (read-only in test mode).

    Args:
        deployment_id: Deployment identifier.
        target_version: Version to rollback to.
    """
    return "Not available in test mode"


# ============================================================
# NOISE TOOLS: Error Tracking (10 tools)
# ============================================================

@mcp.tool()
def list_error_groups(
    project: str,
    status: str = "unresolved",
    sort_by: str = "last_seen",
    limit: int = 25,
) -> str:
    """List error groups for a project.

    Args:
        project: Project slug.
        status: Filter (unresolved, resolved, ignored).
        sort_by: Sort field (last_seen, count, first_seen).
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_error_group_details(
    group_id: str,
) -> str:
    """Get details of an error group.

    Args:
        group_id: Error group identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def get_error_events(
    group_id: str,
    limit: int = 10,
) -> str:
    """Get individual error events for a group.

    Args:
        group_id: Error group identifier.
        limit: Max events.
    """
    return "Not available in test mode"


@mcp.tool()
def get_error_tag_values(
    group_id: str,
    tag_key: str,
) -> str:
    """Get distribution of tag values for an error group.

    Args:
        group_id: Error group identifier.
        tag_key: Tag key to analyze.
    """
    return "Not available in test mode"


@mcp.tool()
def search_errors(
    query: str,
    project: Optional[str] = None,
    limit: int = 25,
) -> str:
    """Search errors across projects.

    Args:
        query: Search query.
        project: Optional project filter.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def list_error_releases(
    project: str,
    limit: int = 10,
) -> str:
    """List releases with error statistics.

    Args:
        project: Project slug.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_error_trends(
    project: str,
    period: str = "24h",
) -> str:
    """Get error count trends over time.

    Args:
        project: Project slug.
        period: Time period (1h, 6h, 24h, 7d, 30d).
    """
    return "Not available in test mode"


@mcp.tool()
def analyze_error_with_ai(
    group_id: str,
) -> str:
    """Get AI-generated analysis of an error group.

    Args:
        group_id: Error group identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def update_error_status(
    group_id: str,
    status: str,
) -> str:
    """Update error group status (read-only in test mode).

    Args:
        group_id: Error group identifier.
        status: New status (resolved, ignored, unresolved).
    """
    return "Not available in test mode"


@mcp.tool()
def list_error_projects() -> str:
    """List available error tracking projects."""
    return "Not available in test mode"


# ============================================================
# NOISE TOOLS: Cloud Infrastructure (10 tools)
# ============================================================

@mcp.tool()
def list_compute_instances(
    region: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List cloud compute instances.

    Args:
        region: Filter by cloud region.
        status: Filter by status (running, stopped, terminated).
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_instance_details(instance_id: str) -> str:
    """Get details of a compute instance.

    Args:
        instance_id: Instance identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def list_containers(
    cluster: Optional[str] = None,
    namespace: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List containers across clusters.

    Args:
        cluster: Filter by cluster name.
        namespace: Filter by namespace.
        status: Filter by status.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_container_logs_cloud(
    container_id: str,
    tail: int = 100,
    since: Optional[str] = None,
) -> str:
    """Get logs from a cloud container.

    Args:
        container_id: Container identifier.
        tail: Number of log lines.
        since: Only logs after this timestamp.
    """
    return "Not available in test mode"


@mcp.tool()
def list_cloud_services(
    region: Optional[str] = None,
    service_type: Optional[str] = None,
) -> str:
    """List managed cloud services.

    Args:
        region: Filter by region.
        service_type: Filter by type (database, cache, queue, storage).
    """
    return "Not available in test mode"


@mcp.tool()
def get_service_health(service_id: str) -> str:
    """Get health status of a cloud service.

    Args:
        service_id: Service identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def list_load_balancers(
    region: Optional[str] = None,
) -> str:
    """List load balancers.

    Args:
        region: Filter by region.
    """
    return "Not available in test mode"


@mcp.tool()
def list_storage_buckets(
    prefix: Optional[str] = None,
) -> str:
    """List cloud storage buckets.

    Args:
        prefix: Filter by bucket name prefix.
    """
    return "Not available in test mode"


@mcp.tool()
def get_bucket_metadata_cloud(bucket_name: str) -> str:
    """Get metadata for a storage bucket.

    Args:
        bucket_name: Bucket name.
    """
    return "Not available in test mode"


@mcp.tool()
def execute_cloud_query(
    query: str,
    region: Optional[str] = None,
    timeout_seconds: int = 30,
) -> str:
    """Execute a cloud resource query (read-only).

    Args:
        query: Resource query in cloud-specific syntax.
        region: Target region.
        timeout_seconds: Query timeout.
    """
    return "Not available in test mode"


# ============================================================
# NOISE TOOLS: Workflow Orchestration (10 tools)
# ============================================================

@mcp.tool()
def list_orchestration_flows(
    project: Optional[str] = None,
    tags: Optional[List[str]] = None,
    limit: int = 20,
) -> str:
    """List workflow orchestration flows.

    Args:
        project: Filter by project.
        tags: Filter by tags.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_orchestration_flow(flow_id: str) -> str:
    """Get details of an orchestration flow.

    Args:
        flow_id: Flow identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def list_flow_runs(
    flow_id: str,
    state: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List runs of a specific flow.

    Args:
        flow_id: Flow identifier.
        state: Filter by state (completed, running, failed, pending).
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def get_flow_run_details(
    flow_run_id: str,
) -> str:
    """Get details of a specific flow run.

    Args:
        flow_run_id: Flow run identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def get_flow_run_logs(
    flow_run_id: str,
    level: str = "INFO",
    limit: int = 100,
) -> str:
    """Get logs from a flow run.

    Args:
        flow_run_id: Flow run identifier.
        level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        limit: Max log entries.
    """
    return "Not available in test mode"


@mcp.tool()
def list_task_runs(
    flow_run_id: str,
    state: Optional[str] = None,
) -> str:
    """List task runs within a flow run.

    Args:
        flow_run_id: Flow run identifier.
        state: Filter by state.
    """
    return "Not available in test mode"


@mcp.tool()
def get_task_run_details(
    task_run_id: str,
) -> str:
    """Get details of a specific task run.

    Args:
        task_run_id: Task run identifier.
    """
    return "Not available in test mode"


@mcp.tool()
def list_work_pools(
    status: Optional[str] = None,
) -> str:
    """List worker pools.

    Args:
        status: Filter by status (online, offline, paused).
    """
    return "Not available in test mode"


@mcp.tool()
def search_orchestration_events(
    query: str,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 50,
) -> str:
    """Search orchestration events.

    Args:
        query: Search query.
        event_type: Filter by event type.
        since: Only events after this ISO 8601 timestamp.
        limit: Max results.
    """
    return "Not available in test mode"


@mcp.tool()
def list_automations(
    status: Optional[str] = None,
) -> str:
    """List automation rules.

    Args:
        status: Filter by status (enabled, disabled).
    """
    return "Not available in test mode"


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")
