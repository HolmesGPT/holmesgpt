"""Local stdio MCP server mocking fetch_resource_issues_metadata (relay's
platform-mcp) for this eval, replacing the old TestSupabaseDal-based mock
(issues_metadata.json), which broke when fetch_resource_issues_metadata
moved server-side to relay in the ROB-415 migration - SupabaseDal.get_issues_metadata
no longer exists to back it.

Scenario, unchanged from the original test: pod analytics-exporter-fast-*
died on 2025-10-19 and no longer exists live. Its full history is a BackOff
warning, a CrashLoopBackoff, and finally an OOMKill - all within the same
minute, no unrelated noise. Holmes must fall back to this historical lookup
since the pod isn't live/found in any (real or absent) cluster.
"""

import json
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Issues Metadata Service")

_POD = "analytics-exporter-fast-76897854c-xxljr"
_NAMESPACE = "default"

_ROWS = [
    {
        "title": f"BackOff Warning for Pod {_NAMESPACE}/{_POD}",
        "subject_name": _POD,
        "subject_namespace": _NAMESPACE,
        "subject_type": "pod",
        "description": (
            f"Back-off restarting failed container memory-eater in pod "
            f"{_POD}_default(dec2ce4b-a210-485d-8e6c-42b2ca28ecde)"
        ),
        "starts_at": "2025-10-19T10:59:27.393256+00:00",
        "aggregation_key": "PodLifecycleWarning",
    },
    {
        "title": f"Crashing pod {_POD} in namespace {_NAMESPACE}",
        "subject_name": _POD,
        "subject_namespace": _NAMESPACE,
        "subject_type": "pod",
        "description": None,
        "starts_at": "2025-10-19T10:59:56.0862+00:00",
        "aggregation_key": "CrashLoopBackoff",
    },
    {
        "title": f"Pod {_POD} in namespace {_NAMESPACE} OOMKilled results",
        "subject_name": _POD,
        "subject_namespace": _NAMESPACE,
        "subject_type": "pod",
        "description": None,
        "starts_at": "2025-10-19T10:59:56.536309+00:00",
        "aggregation_key": "PodOOMKilled",
    },
]

_TOOL_DESCRIPTION = (
    "Fetch issues and alert metadata in a given time range. Can be filtered "
    "by namespace and/or specific Kubernetes resource.\n\n"
    "Args:\n"
    "    start_datetime: RFC3339 start of the search window.\n"
    "    end_datetime: RFC3339 end of the search window.\n"
    "    namespace: Filter by Kubernetes namespace (exact match).\n"
    "    workload: Filter by Kubernetes resource name (exact match).\n"
    "    limit: Maximum number of rows to return.\n"
    "    aggregation_key: Optional list of event types to filter by (exact "
    "match). Not an exhaustive enum - pass any known value; leave empty to "
    "return all issue types. Common examples: PodOOMKilled, CrashLoopBackoff, "
    "FailedScheduling, PodLifecycleWarning, DeadlineExceeded, Unhealthy. Use "
    "this to narrow down a broad time range to a specific kind of event "
    "instead of browsing every issue in the window."
)


@mcp.tool(description=_TOOL_DESCRIPTION)
def fetch_resource_issues_metadata(
    start_datetime: str,
    end_datetime: str,
    namespace: Optional[str] = None,
    workload: Optional[str] = None,
    limit: int = 100,
    aggregation_key: Optional[List[str]] = None,
) -> str:
    rows = _ROWS
    if namespace:
        rows = [r for r in rows if r["subject_namespace"] == namespace]
    if workload:
        rows = [r for r in rows if r["subject_name"] == workload]
    if aggregation_key:
        rows = [r for r in rows if r["aggregation_key"] in aggregation_key]
    rows = rows[:limit]

    if not rows:
        return f"found no data matching the filters: namespace={namespace}, workload={workload}, aggregation_key={aggregation_key}"
    return json.dumps(rows)


if __name__ == "__main__":
    mcp.run()
