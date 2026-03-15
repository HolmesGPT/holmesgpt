"""
MCP server mimicking a large real-world streaming analytics platform with MANY
tools that have overlapping but different parameter structures.

Reproduces production bugs where the LLM confuses parameters between tools:
- Multiple tools use time-related params but with DIFFERENT names/types:
  * `time_range` (object {start, end, minutes}) on timeseries tools
  * `relative_time_interval` (string enum "PT1M"-"PT15M") on flow tools
  * `time_window` (object {from_ts, to_ts, last_hours}) on historical tools
  * `since_minutes` (integer) on summary tools
- Multiple tools use filter-related params but with DIFFERENT types:
  * `filters` (Dict[str, Union[str, List[str]]]) on timeseries/groupby tools
  * `filter` (string, SQL WHERE clause) on flow tools
  * `filter_expression` (string, custom DSL) on ad analytics tools
- Similar metric/dimension params across all tools
- Large enums that bloat token usage (granularity with 50+ values)

The test verifies the LLM can correctly use get_realtime_timeseries with
time_range (object) without confusing it with relative_time_interval (string)
from the flow tools.
"""

from typing import Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("streaming-analytics")


# --- Pydantic models for nested objects ---

class TimeRange(BaseModel):
    """Real-time range (max 15 minutes)."""
    start: Optional[str] = None
    end: Optional[str] = None
    minutes: Optional[int] = None


class HistoricalTimeWindow(BaseModel):
    """Historical time window (up to 90 days)."""
    from_ts: Optional[str] = None
    to_ts: Optional[str] = None
    last_hours: Optional[int] = None


class AlertThreshold(BaseModel):
    """Alert threshold configuration."""
    metric: str
    operator: str
    value: float


# --- Simulated data ---

TIMESERIES_DATA = {
    "bitrate": [
        {"timestamp": "2026-03-15T21:10:00Z", "value": 4500.2},
        {"timestamp": "2026-03-15T21:11:00Z", "value": 4480.7},
        {"timestamp": "2026-03-15T21:12:00Z", "value": 4520.1},
    ],
    "error_rate": [
        {"timestamp": "2026-03-15T21:10:00Z", "value": 0.8},
        {"timestamp": "2026-03-15T21:11:00Z", "value": 0.9},
        {"timestamp": "2026-03-15T21:12:00Z", "value": 0.7},
    ],
    "rebuffer_ratio": [
        {"timestamp": "2026-03-15T21:10:00Z", "value": 0.3},
        {"timestamp": "2026-03-15T21:11:00Z", "value": 0.4},
        {"timestamp": "2026-03-15T21:12:00Z", "value": 0.2},
    ],
}

FLOW_DATA = {
    "checkout": {
        "us-east": {"completion_rate": 87.3, "avg_duration": 12.5},
        "eu-west": {"completion_rate": 82.1, "avg_duration": 14.2},
    },
}

VERIFICATION_CODE_TS = "TS-EVAL-8k3m5n"
VERIFICATION_CODE_FLOW = "FLOW-EVAL-2j7p4r"
VERIFICATION_CODE_GRP = "GRP-EVAL-5w9t1q"
VERIFICATION_CODE_HIST = "HIST-EVAL-3n6v8k"

GRANULARITY_ENUM = [
    "ALL", "PT1M",
    "PT10S", "PT11S", "PT12S", "PT13S", "PT14S", "PT15S",
    "PT16S", "PT17S", "PT18S", "PT19S", "PT20S", "PT21S",
    "PT22S", "PT23S", "PT24S", "PT25S", "PT26S", "PT27S",
    "PT28S", "PT29S", "PT30S", "PT31S", "PT32S", "PT33S",
    "PT34S", "PT35S", "PT36S", "PT37S", "PT38S", "PT39S",
    "PT40S", "PT41S", "PT42S", "PT43S", "PT44S", "PT45S",
    "PT46S", "PT47S", "PT48S", "PT49S", "PT50S", "PT51S",
    "PT52S", "PT53S", "PT54S", "PT55S", "PT56S", "PT57S",
    "PT58S", "PT59S",
]

GRANULARITY_DESC = ", ".join(GRANULARITY_ENUM)

RELATIVE_TIME_ENUM = [
    "PT1M", "PT2M", "PT3M", "PT4M", "PT5M",
    "PT6M", "PT7M", "PT8M", "PT9M", "PT10M",
    "PT11M", "PT12M", "PT13M", "PT14M", "PT15M",
]

HISTORICAL_GRANULARITY_ENUM = [
    "PT1H", "PT6H", "PT12H", "P1D", "P7D", "P30D",
]


# ============================================================
# METADATA TOOLS (3 tools)
# ============================================================

@mcp.tool()
def get_streaming_metrics_metadata() -> str:
    """Get available streaming quality metrics and their descriptions."""
    return (
        "Streaming metrics: bitrate, error_rate, rebuffer_ratio, startup_time, "
        "throughput, concurrent_viewers, session_count, exit_before_video_start, "
        "video_playback_failures, average_frame_rate, connection_induced_rebuffer\n"
        "Use these metric names in any metrics query tool."
    )


@mcp.tool()
def get_streaming_dimensions_metadata() -> str:
    """Get available dimensions for grouping and filtering streaming data."""
    return (
        "Dimensions: cdn_provider, region, device_type, os_name, os_version, "
        "browser_name, browser_version, content_type, content_title, isp, "
        "stream_protocol, player_version, geo_country_code, geo_city, "
        "network_type, resolution, cdn_edge_server\n"
        f"Granularities for real-time timeseries: {GRANULARITY_DESC}\n"
        "Relative intervals for flow queries: " + ", ".join(RELATIVE_TIME_ENUM) + "\n"
        "Historical granularities: " + ", ".join(HISTORICAL_GRANULARITY_ENUM)
    )


@mcp.tool()
def get_flow_models_metadata() -> str:
    """Get available user journey flow models and their dimensions."""
    return (
        "Flow models:\n"
        "  checkout: User checkout journey (dimensions: region, device_type, payment_method)\n"
        "  onboarding: New user onboarding (dimensions: region, referral_source, plan_type)\n"
        "  content_discovery: Content browsing journey (dimensions: genre, device_type, region)\n"
        "  playback_start: Video start journey (dimensions: cdn_provider, region, content_type)\n"
        "Use flow_model_id parameter with flow query tools."
    )


# ============================================================
# REAL-TIME TIMESERIES TOOLS (3 tools) — use TimeRange object
# ============================================================

@mcp.tool()
def get_realtime_timeseries(
    metrics: List[str],
    time_range: TimeRange,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    account_name: Optional[str] = None,
) -> str:
    """Query real-time streaming metrics as time series data points.

    Args:
        metrics: Array of metric names to query (1-12 metrics).
        time_range: Time range object. Provide either 'minutes' (number, 1-15)
            OR 'start'/'end' as ISO 8601 timestamps.
        granularity: Time bucket size. One of: ALL, PT1M, PT10S-PT59S.
        filters: Key-value filter map. Keys are dimension names, values are
            strings or arrays for multi-select.
        account_name: Account override.
    """
    if not isinstance(time_range, TimeRange):
        return (
            f"Error: time_range must be object with minutes or start/end, "
            f"got {type(time_range).__name__}: {time_range!r}"
        )
    if time_range.minutes is None and time_range.start is None:
        return (
            f"Error: time_range needs 'minutes' or 'start'/'end'. "
            f"Got: {time_range.model_dump()}"
        )

    lines = [f"Verification: {VERIFICATION_CODE_TS}"]
    lines.append(f"Query: metrics={metrics}, time_range={time_range.model_dump()}")

    if filters and isinstance(filters, dict):
        lines.append(f"Filters: {filters}")

    for metric in metrics:
        data = TIMESERIES_DATA.get(metric, [])
        if not data:
            lines.append(f"  {metric}: no data")
            continue
        lines.append(f"  {metric}:")
        for pt in data:
            lines.append(f"    {pt['timestamp']}: {pt['value']}")
    return "\n".join(lines)


@mcp.tool()
def get_realtime_timeseries_comparison(
    metrics: List[str],
    time_range: TimeRange,
    compare_dimension: str,
    compare_values: List[str],
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> str:
    """Compare real-time metrics across dimension values as time series.

    Args:
        metrics: Array of metric names to query.
        time_range: Time range object with minutes or start/end.
        compare_dimension: Dimension to compare across.
        compare_values: List of dimension values to compare.
        granularity: Time bucket size.
        filters: Additional filters.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    lines = [f"Verification: {VERIFICATION_CODE_TS}-CMP"]
    lines.append(f"Comparison: {compare_dimension} = {compare_values}")
    for metric in metrics:
        lines.append(f"  {metric}: comparison data not available in test mode")
    return "\n".join(lines)


@mcp.tool()
def get_realtime_percentiles(
    metrics: List[str],
    time_range: TimeRange,
    percentiles: List[int],
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> str:
    """Get percentile breakdowns for real-time metrics.

    Args:
        metrics: Array of metric names.
        time_range: Time range object with minutes or start/end.
        percentiles: List of percentile values (e.g. [50, 90, 95, 99]).
        granularity: Time bucket size.
        filters: Key-value filter map.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    return f"Verification: {VERIFICATION_CODE_TS}-PCT\nPercentile data not available in test mode"


# ============================================================
# REAL-TIME GROUP-BY TOOLS (3 tools) — also use TimeRange object + filters
# ============================================================

@mcp.tool()
def get_realtime_group_by(
    metrics: List[str],
    dimension: str,
    time_range: TimeRange,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    limit: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> str:
    """Query real-time metrics grouped by a single dimension.

    Args:
        metrics: Array of metric names.
        dimension: Dimension to group by.
        time_range: Time range object with minutes or start/end.
        granularity: Time bucket size.
        filters: Key-value filter map.
        limit: Max rows (default 50).
        sort_by: Metric to sort by.
        sort_order: 'asc' or 'desc'.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    lines = [f"Verification: {VERIFICATION_CODE_GRP}"]
    lines.append(f"GroupBy: {dimension}, metrics={metrics}")
    return "\n".join(lines)


@mcp.tool()
def get_realtime_multi_group_by(
    metrics: List[str],
    dimensions: List[str],
    time_range: TimeRange,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    limit: Optional[int] = None,
) -> str:
    """Query real-time metrics grouped by multiple dimensions.

    Args:
        metrics: Array of metric names.
        dimensions: Array of dimensions to group by.
        time_range: Time range object with minutes or start/end.
        granularity: Time bucket size.
        filters: Key-value filter map.
        limit: Max rows.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    return f"Verification: {VERIFICATION_CODE_GRP}-MULTI\nMulti-group data not available"


@mcp.tool()
def get_realtime_top_n(
    metric: str,
    dimension: str,
    time_range: TimeRange,
    n: int,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> str:
    """Get top N dimension values by a single metric in real-time.

    Args:
        metric: Single metric name to rank by.
        dimension: Dimension to rank.
        time_range: Time range object with minutes or start/end.
        n: Number of top values.
        filters: Key-value filter map.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    return f"Verification: {VERIFICATION_CODE_GRP}-TOP\nTop-N data not available"


# ============================================================
# FLOW TOOLS (4 tools) — use relative_time_interval (string enum)
# ============================================================

@mcp.tool()
def get_flow_realtime_grouped(
    flow_model_id: str,
    group_by_dimension: str,
    relative_time_interval: str,
    filter: Optional[str] = None,
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    order_direction: Optional[str] = None,
) -> str:
    """Query real-time user journey flow metrics grouped by dimension.

    Args:
        flow_model_id: Flow model identifier from get_flow_models_metadata.
        group_by_dimension: Dimension to group by.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        filter: SQL WHERE clause for filtering.
        limit: Max rows (default 50, max 500).
        order_by: Metric to sort by.
        order_direction: 'asc' or 'desc'.
    """
    lines = [f"Verification: {VERIFICATION_CODE_FLOW}"]
    lines.append(f"Flow grouped: {flow_model_id} by {group_by_dimension}")
    flow = FLOW_DATA.get(flow_model_id, {})
    for dim_val, m in flow.items():
        lines.append(f"  {dim_val}: {m}")
    return "\n".join(lines)


@mcp.tool()
def get_flow_realtime_timeseries(
    flow_model_id: str,
    flow_metrics: List[str],
    relative_time_interval: str,
    filter: Optional[str] = None,
    granularity: Optional[str] = None,
) -> str:
    """Query real-time flow metrics as time series.

    Args:
        flow_model_id: Flow model identifier.
        flow_metrics: Array of flow metric names.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        filter: SQL WHERE clause.
        granularity: Time bucket size.
    """
    return f"Verification: {VERIFICATION_CODE_FLOW}-TS\nFlow timeseries not available"


@mcp.tool()
def get_flow_realtime_summary(
    flow_model_id: str,
    relative_time_interval: str,
    filter: Optional[str] = None,
) -> str:
    """Get summary statistics for a flow model in real-time.

    Args:
        flow_model_id: Flow model identifier.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        filter: SQL WHERE clause.
    """
    return f"Verification: {VERIFICATION_CODE_FLOW}-SUM\nFlow summary not available"


@mcp.tool()
def get_flow_comparison(
    flow_model_id: str,
    compare_dimension: str,
    compare_values: List[str],
    relative_time_interval: str,
    filter: Optional[str] = None,
) -> str:
    """Compare flow metrics across dimension values.

    Args:
        flow_model_id: Flow model identifier.
        compare_dimension: Dimension to compare.
        compare_values: Values to compare.
        relative_time_interval: ISO 8601 duration (PT1M-PT15M).
        filter: SQL WHERE clause.
    """
    return f"Verification: {VERIFICATION_CODE_FLOW}-CMP\nFlow comparison not available"


# ============================================================
# HISTORICAL TOOLS (3 tools) — use HistoricalTimeWindow object
# ============================================================

@mcp.tool()
def get_historical_timeseries(
    metrics: List[str],
    time_window: HistoricalTimeWindow,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> str:
    """Query historical streaming metrics (up to 90 days).

    Args:
        metrics: Array of metric names.
        time_window: Historical window. Provide 'last_hours' or 'from_ts'/'to_ts'.
        granularity: One of: PT1H, PT6H, PT12H, P1D, P7D, P30D.
        filters: Key-value filter map.
    """
    if not isinstance(time_window, HistoricalTimeWindow):
        return f"Error: time_window must be object, got {type(time_window).__name__}"
    return f"Verification: {VERIFICATION_CODE_HIST}\nHistorical data not available"


@mcp.tool()
def get_historical_group_by(
    metrics: List[str],
    dimension: str,
    time_window: HistoricalTimeWindow,
    granularity: Optional[str] = None,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    limit: Optional[int] = None,
) -> str:
    """Query historical metrics grouped by dimension (up to 90 days).

    Args:
        metrics: Array of metric names.
        dimension: Dimension to group by.
        time_window: Historical window with last_hours or from_ts/to_ts.
        granularity: Historical granularity.
        filters: Key-value filter map.
        limit: Max rows.
    """
    if not isinstance(time_window, HistoricalTimeWindow):
        return f"Error: time_window must be object, got {type(time_window).__name__}"
    return f"Verification: {VERIFICATION_CODE_HIST}-GRP\nHistorical grouped not available"


@mcp.tool()
def get_historical_comparison(
    metrics: List[str],
    time_window: HistoricalTimeWindow,
    compare_time_window: HistoricalTimeWindow,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> str:
    """Compare metrics across two historical time windows.

    Args:
        metrics: Array of metric names.
        time_window: Primary time window.
        compare_time_window: Comparison time window.
        filters: Key-value filter map.
    """
    return f"Verification: {VERIFICATION_CODE_HIST}-CMP\nComparison not available"


# ============================================================
# AD ANALYTICS TOOLS (3 tools) — use filter_expression (string DSL)
# ============================================================

@mcp.tool()
def get_ad_realtime_metrics(
    ad_metrics: List[str],
    time_range: TimeRange,
    filter_expression: Optional[str] = None,
    granularity: Optional[str] = None,
) -> str:
    """Query real-time ad performance metrics.

    Args:
        ad_metrics: Array of ad metric names (ad_impressions, ad_completion_rate, etc).
        time_range: Time range object with minutes or start/end.
        filter_expression: Filter DSL expression (e.g. "campaign_id=abc AND placement=pre_roll").
        granularity: Time bucket size.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    return "Ad metrics not available in test mode"


@mcp.tool()
def get_ad_realtime_group_by(
    ad_metrics: List[str],
    dimension: str,
    time_range: TimeRange,
    filter_expression: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Query ad metrics grouped by dimension.

    Args:
        ad_metrics: Array of ad metric names.
        dimension: Dimension to group by.
        time_range: Time range object.
        filter_expression: Filter DSL expression.
        limit: Max rows.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    return "Ad grouped metrics not available in test mode"


@mcp.tool()
def get_ad_campaign_summary(
    campaign_id: str,
    since_minutes: int,
) -> str:
    """Get summary for a specific ad campaign.

    Args:
        campaign_id: Campaign identifier.
        since_minutes: Minutes of data to include (1-60).
    """
    return "Ad campaign summary not available in test mode"


# ============================================================
# ALERT / THRESHOLD TOOLS (3 tools) — use various time params
# ============================================================

@mcp.tool()
def get_active_alerts(
    since_minutes: Optional[int] = None,
    severity: Optional[str] = None,
) -> str:
    """Get currently active alerts.

    Args:
        since_minutes: Only show alerts from last N minutes.
        severity: Filter by severity (critical, warning, info).
    """
    return "No active alerts in test mode"


@mcp.tool()
def get_alert_history(
    time_window: HistoricalTimeWindow,
    severity: Optional[str] = None,
    metric: Optional[str] = None,
) -> str:
    """Get alert history.

    Args:
        time_window: Historical window with last_hours or from_ts/to_ts.
        severity: Filter by severity.
        metric: Filter by metric name.
    """
    return "Alert history not available in test mode"


@mcp.tool()
def configure_alert_threshold(
    threshold: AlertThreshold,
    notify_channels: Optional[List[str]] = None,
) -> str:
    """Set an alert threshold for a metric (read-only in test mode).

    Args:
        threshold: Alert threshold with metric, operator, value.
        notify_channels: List of notification channel IDs.
    """
    return "Alert configuration not available in test mode"


# ============================================================
# SUMMARY / CONVENIENCE TOOLS (3 tools)
# ============================================================

@mcp.tool()
def get_quality_overview(
    since_minutes: Optional[int] = None,
) -> str:
    """Get overall streaming quality overview. Simple convenience tool.

    Args:
        since_minutes: Minutes of data (default 5).
    """
    return (
        "Quality Overview (last 5 min): bitrate=4500.3, errors=0.8%, "
        "rebuffer=0.3%, viewers=95800. "
        "Use get_realtime_timeseries for detailed data."
    )


@mcp.tool()
def get_cdn_performance_summary(
    since_minutes: Optional[int] = None,
    cdn_providers: Optional[List[str]] = None,
) -> str:
    """Get CDN provider performance summary.

    Args:
        since_minutes: Minutes of data (default 5).
        cdn_providers: Optional list of CDN providers to include.
    """
    return "CDN summary: cloudfront=4500kbps, akamai=4200kbps, fastly=3800kbps"


@mcp.tool()
def get_viewer_experience_score(
    time_range: TimeRange,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> str:
    """Calculate composite viewer experience score.

    Args:
        time_range: Time range object with minutes or start/end.
        filters: Key-value filter map.
    """
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be object, got {type(time_range).__name__}"
    return "Experience score: 87.3/100 (Good)"


if __name__ == "__main__":
    mcp.run(transport="stdio")
