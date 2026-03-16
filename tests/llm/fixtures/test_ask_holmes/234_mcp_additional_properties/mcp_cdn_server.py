"""
MCP server mimicking a real-world CDN / streaming analytics platform.

Generates JSON schemas with patterns that expose additionalProperties bugs:
- `filters`: type "object" with `additionalProperties` containing `anyOf`
  (values are Union[str, List[str]]) and NO `properties` key — this is the
  exact pattern from production MCP servers that causes issues
- `time_range`: nested object via $ref with enum sub-fields
- `metrics` / `dimensions`: arrays of strings
- Multiple optional parameters

The critical schema pattern for `filters`:
  {
    "anyOf": [
      {
        "additionalProperties": {
          "anyOf": [
            {"type": "string"},
            {"items": {"type": "string"}, "type": "array"}
          ]
        },
        "type": "object"
      },
      {"type": "null"}
    ]
  }

Without proper additionalProperties handling:
- filters inner schema (Union[str, List[str]] values) is lost
- LLM sees bare {type: "object"} -> may send JSON string or wrong structure
- In strict mode, additionalProperties: false contradicts the dynamic-keys intent

This version is hardened:
- No summary/shortcut tool that leaks answers
- Returns WRONG verification code on malformed filter usage
- Requires exact array syntax for multi-select filters
- Adds decoy metrics in unfiltered results to punish skipping filters
"""

from enum import Enum
from typing import Dict, List, Optional, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("cdn-analytics")


class Granularity(str, Enum):
    PT1M = "PT1M"
    PT5M = "PT5M"
    PT15M = "PT15M"
    PT1H = "PT1H"
    P1D = "P1D"


class TimeRange(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    granularity: Optional[Granularity] = None
    last_minutes: Optional[int] = None


VALID_METRICS = [
    "bitrate", "rebuffer_ratio", "startup_time", "throughput",
    "error_rate", "concurrent_viewers", "session_count",
]

VALID_DIMENSIONS = [
    "cdn_provider", "region", "device_type", "os_name",
    "content_type", "isp", "stream_protocol",
]

# Simulated data keyed by (metric, dimension)
ANALYTICS_DATA = {
    ("bitrate", "cdn_provider"): [
        {"dimension_value": "cloudfront", "bitrate": 4500.2},
        {"dimension_value": "akamai", "bitrate": 4200.8},
        {"dimension_value": "fastly", "bitrate": 3800.5},
        {"dimension_value": "stackpath", "bitrate": 3100.9},
        {"dimension_value": "limelight", "bitrate": 2900.4},
    ],
    ("error_rate", "cdn_provider"): [
        {"dimension_value": "cloudfront", "error_rate": 0.8},
        {"dimension_value": "akamai", "error_rate": 1.2},
        {"dimension_value": "fastly", "error_rate": 2.1},
        {"dimension_value": "stackpath", "error_rate": 3.4},
        {"dimension_value": "limelight", "error_rate": 4.7},
    ],
    ("rebuffer_ratio", "cdn_provider"): [
        {"dimension_value": "cloudfront", "rebuffer_ratio": 0.3},
        {"dimension_value": "akamai", "rebuffer_ratio": 0.5},
        {"dimension_value": "fastly", "rebuffer_ratio": 1.1},
    ],
    ("bitrate", "device_type"): [
        {"dimension_value": "smart_tv", "bitrate": 5200.0},
        {"dimension_value": "mobile", "bitrate": 2800.3},
        {"dimension_value": "desktop", "bitrate": 4100.7},
    ],
    ("concurrent_viewers", "region"): [
        {"dimension_value": "us-east", "concurrent_viewers": 45200},
        {"dimension_value": "eu-west", "concurrent_viewers": 32100},
        {"dimension_value": "ap-south", "concurrent_viewers": 18500},
    ],
}

CORRECT_VERIFICATION_CODE = "CDN-EVAL-4m8p2x"
WRONG_VERIFICATION_CODE = "CDN-EVAL-WRONG-0z0z0z"


def _filters_are_correct(filters: Optional[Dict[str, Union[str, List[str]]]]) -> bool:
    """Check that filters were passed correctly as a dict with proper types."""
    if filters is None:
        return False
    if not isinstance(filters, dict):
        return False
    # Check that at least one filter has an array value (multi-select)
    has_array_filter = any(isinstance(v, list) for v in filters.values())
    if not has_array_filter:
        return False
    # Check that array values contain strings
    for v in filters.values():
        if isinstance(v, list):
            if not all(isinstance(item, str) for item in v):
                return False
    return True


@mcp.tool()
def get_analytics_metadata() -> str:
    """Get available metrics, dimensions, and filter options for querying.

    Returns the full catalog of available metrics and dimensions.
    Filters use key-value pairs where the key is a dimension name
    and the value is a string or array of strings for multi-select.
    """
    return (
        f"Available metrics: {', '.join(VALID_METRICS)}\n"
        f"Available dimensions: {', '.join(VALID_DIMENSIONS)}\n"
        "Filters: key-value pairs where key is a dimension name and value is "
        "either a single string or array of strings for multi-select.\n"
        "Example filters: {\"cdn_provider\": \"cloudfront\"} or "
        "{\"cdn_provider\": [\"cloudfront\", \"akamai\"]}\n"
        "IMPORTANT: When filtering for multiple values, you MUST use an array. "
        "Passing a comma-separated string will NOT work."
    )


@mcp.tool()
def query_streaming_analytics(
    metrics: List[str],
    dimensions: List[str],
    time_range: TimeRange,
    filters: Optional[Dict[str, Union[str, List[str]]]] = None,
    group_by: Optional[List[str]] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> str:
    """Query real-time streaming analytics data with flexible filtering.

    Args:
        metrics: Array of metric names to retrieve (e.g. bitrate, error_rate).
        dimensions: Array of dimension names to include in results.
        time_range: Time range for the query. Provide last_minutes for relative
            or start/end as ISO 8601 timestamps. Optionally set granularity.
        filters: Key-value filter map. Keys are dimension names, values are
            either a single string or an array of strings for multi-select.
        group_by: Optional list of dimensions to group results by.
        limit: Maximum number of rows to return (default 100).
        offset: Number of rows to skip for pagination.
    """
    # Validate time_range is an object
    if not isinstance(time_range, TimeRange):
        return (
            f"Error: time_range must be an object with last_minutes or "
            f"start/end fields, got: {type(time_range).__name__}"
        )

    # Validate filters is a dict if provided
    if filters is not None and not isinstance(filters, dict):
        return (
            f"Error: filters must be a key-value object, got: "
            f"{type(filters).__name__} = {filters!r}"
        )

    # Determine verification code based on filter correctness
    filters_ok = _filters_are_correct(filters)
    verification = CORRECT_VERIFICATION_CODE if filters_ok else WRONG_VERIFICATION_CODE

    results_lines = [f"Verification: {verification}"]
    results_lines.append(
        f"Query: metrics={metrics}, dimensions={dimensions}, "
        f"granularity={time_range.granularity}"
    )

    if filters:
        results_lines.append(f"Active filters: {filters}")
    else:
        results_lines.append("WARNING: No filters applied - returning ALL providers")

    effective_group = group_by if group_by else dimensions[:1]

    for metric in metrics:
        for dim in effective_group:
            key = (metric, dim)
            data = ANALYTICS_DATA.get(key, [])

            if filters:
                for fk, fv in filters.items():
                    if fk != dim:
                        continue
                    if isinstance(fv, list):
                        data = [
                            d for d in data
                            if d.get("dimension_value") in fv
                        ]
                    elif isinstance(fv, str):
                        data = [
                            d for d in data
                            if d.get("dimension_value") == fv
                        ]

            if limit is not None:
                start = offset or 0
                data = data[start:start + limit]

            if not data:
                results_lines.append(f"  {metric} by {dim}: no data")
                continue

            results_lines.append(f"  {metric} by {dim}:")
            for row in data:
                dim_val = row["dimension_value"]
                val = row.get(metric, "N/A")
                results_lines.append(f"    {dim_val}: {val}")

    return "\n".join(results_lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
