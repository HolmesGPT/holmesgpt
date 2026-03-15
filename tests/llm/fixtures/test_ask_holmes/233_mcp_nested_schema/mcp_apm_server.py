"""
MCP server mimicking a real-world APM (Application Performance Monitoring) platform.

Generates complex JSON schemas with patterns found in production MCP servers:
- $ref to nested objects (TimeRange) containing their own anyOf fields
- anyOf wrapping $ref to enums (Granularity, SortOrder) — double indirection
- anyOf wrapping objects with additionalProperties (filters)
- Arrays with items constraints (metrics list)
- Multiple required + optional parameters

Without _resolve_schema:
- time_range ($ref → TimeRange) defaults to type "string" → server rejects
- granularity (anyOf → $ref → Granularity enum) defaults to "string" → loses enum
- filters (anyOf → object with additionalProperties) defaults to "string"
- order (anyOf → $ref → SortOrder enum) defaults to "string" → loses enum
"""

from enum import Enum
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("apm-analytics")


class TimeRange(BaseModel):
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    minutes: Optional[int] = None


class Granularity(str, Enum):
    ALL = "ALL"
    PT1M = "PT1M"
    PT5M = "PT5M"
    PT15M = "PT15M"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


# Simulated APM data: grouped metrics by dimension
GROUPED_DATA = {
    ("response_time", "service_name"): [
        {"dimension_value": "checkout-api", "response_time": 245.3},
        {"dimension_value": "user-service", "response_time": 89.7},
        {"dimension_value": "inventory-db", "response_time": 512.1},
        {"dimension_value": "payment-gateway", "response_time": 178.4},
    ],
    ("error_rate", "service_name"): [
        {"dimension_value": "checkout-api", "error_rate": 2.3},
        {"dimension_value": "user-service", "error_rate": 0.1},
        {"dimension_value": "inventory-db", "error_rate": 5.7},
        {"dimension_value": "payment-gateway", "error_rate": 1.8},
    ],
    ("throughput", "region"): [
        {"dimension_value": "us-east-1", "throughput": 15234},
        {"dimension_value": "eu-west-1", "throughput": 8721},
        {"dimension_value": "ap-south-1", "throughput": 4102},
    ],
}

VERIFICATION_CODE = "APM-EVAL-9r3k7w"


@mcp.tool()
def get_metrics_metadata() -> str:
    """Get available metrics and dimensions for querying."""
    return (
        "Available metrics: response_time, error_rate, throughput, cpu_usage, memory_usage\n"
        "Available dimensions: service_name, region, environment, pod_name\n"
        "Available granularities: ALL, PT1M, PT5M, PT15M"
    )


@mcp.tool()
def query_grouped_metrics(
    metrics: List[str],
    dimension: str,
    time_range: TimeRange,
    granularity: Optional[Granularity] = None,
    filters: Optional[Dict[str, str]] = None,
    limit: Optional[int] = None,
    sort_by: Optional[str] = None,
    order: Optional[SortOrder] = None,
) -> str:
    """Query real-time metrics grouped by a dimension.

    Args:
        metrics: Array of metric names to query (e.g. response_time, error_rate).
        dimension: Single dimension name to group results by (e.g. service_name, region).
        time_range: Time range for the query. Provide either minutes (relative) or start_time/end_time (absolute ISO 8601).
        granularity: Time bucket size. One of ALL, PT1M, PT5M, PT15M. Defaults to ALL.
        filters: Optional key-value filters to narrow results. Keys are dimension names.
        limit: Maximum number of dimension values to return (default 50).
        sort_by: Metric name to sort results by. Must be one of the requested metrics.
        order: Sort direction when sort_by is specified. One of asc, desc.
    """
    # Validate time_range is an object (not a string)
    if not isinstance(time_range, TimeRange):
        return f"Error: time_range must be an object with minutes or start_time/end_time fields, got: {type(time_range).__name__}"

    results_lines = [f"Verification: {VERIFICATION_CODE}"]
    results_lines.append(f"Query: metrics={metrics}, dimension={dimension}, granularity={granularity}")

    for metric in metrics:
        key = (metric, dimension)
        data = GROUPED_DATA.get(key, [])

        if filters:
            for fk, fv in filters.items():
                data = [d for d in data if d.get("dimension_value") != fv or fk != dimension]

        if sort_by and sort_by in metrics:
            data = sorted(data, key=lambda x: x.get(sort_by, 0), reverse=(order != SortOrder.ASC if order else True))

        if limit is not None:
            data = data[:limit]

        if not data:
            results_lines.append(f"  {metric} by {dimension}: no data")
            continue

        results_lines.append(f"  {metric} by {dimension}:")
        for row in data:
            dim_val = row["dimension_value"]
            val = row.get(metric, "N/A")
            results_lines.append(f"    {dim_val}: {val}")

    return "\n".join(results_lines)


@mcp.tool()
def get_top_services_summary() -> str:
    """Get a quick summary of top services by response time. No parameters needed."""
    return (
        f"Top services by response time (last 5 min):\n"
        f"  1. inventory-db: 512.1ms\n"
        f"  2. checkout-api: 245.3ms\n"
        f"  3. payment-gateway: 178.4ms\n"
        f"  4. user-service: 89.7ms\n"
        f"Average response time across all services: 256.375ms\n"
        f"Note: Use query_grouped_metrics for detailed breakdowns with filters and sorting."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
