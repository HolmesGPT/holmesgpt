"""Local stdio MCP server mocking fetch_resource_issues_metadata's NEW
aggregation_key filter (relay/pkg/apps/mcp/tools/platform_data.py) so evals
can verify Holmes actually uses it, without a live platform-mcp stack.

Scenario: a namespace with 15 recent, unrelated noise events (DeadlineExceeded
/ FailedScheduling / CrashLoopBackoff / Unhealthy) and exactly one older
OOMKilled event carrying a unique verification code. The mock enforces a
hard row cap (HARD_CAP) regardless of the requested `limit`, mirroring
relay's real MAX_LIMIT_CHANGE_ROWS behavior - so an unfiltered, recency-sorted
query can never surface the OOM row: only a query that actually passes
aggregation_key=["PodOOMKilled"] (or an equivalent value) does. This makes
the eval's pass/fail condition an objective proof of correct usage, not a
cosmetic check of the tool-call shape.
"""

import json
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Issues Metadata Service")

HARD_CAP = 8
VERIFICATION_CODE = "OOM-EVAL-7f3k9x"

# Ordered most-recent-first, matching the real tool's `starts_at desc`.
_NOISE = [
    {
        "title": "DeadlineExceeded Warning for Job checkout-service/cron-report-29744980",
        "subject_name": "cron-report-29744980",
        "subject_namespace": "checkout-service",
        "subject_type": "job",
        "description": "Job was active longer than specified deadline",
        "starts_at": f"2026-07-2{9 - i}T0{i}:15:00+00:00",
        "aggregation_key": "DeadlineExceeded",
    }
    for i in range(6)
] + [
    {
        "title": "FailedScheduling Warning for Pod checkout-service/checkout-worker-7f9b8c6d5-xk2p1",
        "subject_name": "checkout-worker-7f9b8c6d5-xk2p1",
        "subject_namespace": "checkout-service",
        "subject_type": "pod",
        "description": "0/3 nodes are available: 3 Insufficient cpu.",
        "starts_at": f"2026-07-2{3 - i}T1{i}:40:00+00:00",
        "aggregation_key": "FailedScheduling",
    }
    for i in range(5)
] + [
    {
        "title": "Unhealthy Warning for Pod checkout-service/checkout-worker-7f9b8c6d5-xk2p1",
        "subject_name": "checkout-worker-7f9b8c6d5-xk2p1",
        "subject_namespace": "checkout-service",
        "subject_type": "pod",
        "description": "Readiness probe failed: connection refused",
        "starts_at": f"2026-07-1{8 - i}T0{i}:05:00+00:00",
        "aggregation_key": "Unhealthy",
    }
    for i in range(4)
]

_OOM_ROW = {
    "title": "Pod checkout-worker-7f9b8c6d5-xk2p1 in namespace checkout-service OOMKilled results",
    "subject_name": "checkout-worker-7f9b8c6d5-xk2p1",
    "subject_namespace": "checkout-service",
    "subject_type": "pod",
    "description": f"Container main was OOMKilled. verification_code={VERIFICATION_CODE}",
    "starts_at": "2026-07-05T02:10:00+00:00",
    "aggregation_key": "PodOOMKilled",
}

_ALL_ROWS = _NOISE + [_OOM_ROW]

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
    rows = _ALL_ROWS
    if namespace:
        rows = [r for r in rows if r["subject_namespace"] == namespace]
    if workload:
        rows = [r for r in rows if r["subject_name"] == workload]
    if aggregation_key:
        rows = [r for r in rows if r["aggregation_key"] in aggregation_key]

    effective_limit = min(limit, HARD_CAP)
    rows = rows[:effective_limit]

    if not rows:
        return f"found no data matching the filters: namespace={namespace}, workload={workload}, aggregation_key={aggregation_key}"
    return json.dumps(rows)


if __name__ == "__main__":
    mcp.run()
