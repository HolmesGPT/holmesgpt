"""
MCP metrics server that uses Pydantic models with Optional types.

FastMCP generates JSON schemas with $ref, anyOf, and $defs for these models,
which exercises the schema resolution logic in HolmesGPT's MCP toolset parser.

Schema patterns generated:
- MetricFilter parameter → $ref: "#/$defs/MetricFilter" (requires $ref resolution)
- Optional[str] fields → anyOf: [{type: string}, {type: null}] (requires anyOf resolution)
- Optional[int] top-level param → anyOf: [{type: integer}, {type: null}] (requires anyOf resolution)
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("metrics-db")


class MetricFilter(BaseModel):
    namespace: str
    pod_name: Optional[str] = None
    min_value: Optional[float] = None


METRICS_DATA = [
    {"name": "cpu_usage", "namespace": "app-232", "pod": "web-server-1", "value": 65.0, "timestamp": "2026-03-15T10:00:00Z"},
    {"name": "cpu_usage", "namespace": "app-232", "pod": "web-server-2", "value": 82.0, "timestamp": "2026-03-15T10:00:00Z"},
    {"name": "cpu_usage", "namespace": "app-232", "pod": "worker-1", "value": 73.5, "timestamp": "2026-03-15T10:00:00Z"},
    {"name": "memory_usage", "namespace": "app-232", "pod": "web-server-1", "value": 512.0, "timestamp": "2026-03-15T10:00:00Z"},
    {"name": "cpu_usage", "namespace": "default", "pod": "nginx-1", "value": 10.0, "timestamp": "2026-03-15T10:00:00Z"},
]

VERIFICATION_CODE = "HOLMES-MCP-7k9x2m"


@mcp.tool()
def query_metrics(metric_name: str, filter: MetricFilter, limit: Optional[int] = None) -> str:
    """Query metrics by name with filters.

    Args:
        metric_name: Name of the metric to query (e.g. cpu_usage, memory_usage)
        filter: Filter criteria including namespace (required), pod_name (optional), and min_value (optional)
        limit: Maximum number of results to return
    """
    results = []
    for m in METRICS_DATA:
        if m["name"] != metric_name:
            continue
        if m["namespace"] != filter.namespace:
            continue
        if filter.pod_name and m["pod"] != filter.pod_name:
            continue
        if filter.min_value is not None and m["value"] < filter.min_value:
            continue
        results.append(m)

    if limit is not None:
        results = results[:limit]

    if not results:
        return f"No metrics found for metric_name={metric_name} in namespace={filter.namespace}"

    lines = [f"Verification code: {VERIFICATION_CODE}", f"Found {len(results)} metrics:"]
    for r in results:
        lines.append(f"  - pod={r['pod']} value={r['value']} at {r['timestamp']}")
    return "\n".join(lines)


@mcp.tool()
def get_metric_summary(namespace: str, since_minutes: Optional[int] = None) -> str:
    """Get a summary of all metrics in a namespace.

    Args:
        namespace: The namespace to get metrics for
        since_minutes: Only include metrics from the last N minutes
    """
    results = [m for m in METRICS_DATA if m["namespace"] == namespace]

    if not results:
        return f"No metrics found in namespace {namespace}"

    values = [m["value"] for m in results]
    avg_value = sum(values) / len(values)

    lines = [
        f"Metric summary for namespace {namespace}:",
        f"  Total metrics: {len(results)}",
        f"  Average value: {avg_value}",
        f"  Verification: {VERIFICATION_CODE}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
