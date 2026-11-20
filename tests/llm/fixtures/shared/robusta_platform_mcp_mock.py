"""Stdio mock of the Robusta platform MCP server's data tools.

Serves fetch_resource_issues_metadata, fetch_configuration_changes_metadata and
fetch_resource_recommendation with the production tool names, descriptions and
input schemas, so the LLM sees the same tool surface it gets from platform-mcp.
They are a copy of that contract: update them when it changes.
fetch_resource_recommendation always returns no recommendations. The other two
read rows from JSON fixtures in the directory passed as the first argument:

  issues_metadata.json    rows for fetch_resource_issues_metadata
  changes_metadata.json   rows for fetch_configuration_changes_metadata

Each row is an Issues-table row (see 18_oom_kill_from_issues_history). The
filters, projection, ordering and empty-result text follow the server's: rows
whose creation_date falls in [start_datetime, end_datetime], exact matches on
subject_name (workload) and subject_namespace (namespace), the current cluster
unless clusters/all_clusters say otherwise, newest starts_at first.

Usage in toolsets.yaml:

  mcp_servers:
    robusta_platform_mcp:
      config:
        mode: stdio
        command: python
        args:
          - tests/llm/fixtures/shared/robusta_platform_mcp_mock.py
          - tests/llm/fixtures/test_ask_holmes/<test>
          - --cluster=<current cluster name>
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import mcp.types as types
from dateutil.parser import isoparse
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

DEFAULT_LIMIT_CHANGE_ROWS = 100
MAX_LIMIT_CHANGE_ROWS = 200
EXTERNAL_CLUSTER = "external"

ISSUE_COLUMNS = (
    "id",
    "title",
    "subject_name",
    "subject_namespace",
    "subject_type",
    "description",
    "starts_at",
    "ends_at",
    "cluster",
)

_START_TIME_SCHEMA = {
    "type": "string",
    "description": "The starting time boundary for the search period. String in RFC3339 format.",
}
_END_TIME_SCHEMA = {
    "type": "string",
    "description": "The ending time boundary for the search period. String in RFC3339 format.",
}
_LIMIT_SCHEMA = {
    "type": "integer",
    "description": f"Maximum number of rows to return. Default is {DEFAULT_LIMIT_CHANGE_ROWS} and the maximum is {MAX_LIMIT_CHANGE_ROWS}.",
}
_CLUSTERS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Optional list of specific cluster names to query. If provided, overrides all_clusters. Use this to query a specific subset of clusters. Example: ['prod-us-east', 'prod-us-west']. Leave empty to use default behavior (current cluster or all clusters based on all_clusters flag).",
}


def _all_clusters_schema(subject: str) -> Dict[str, Any]:
    return {
        "type": "boolean",
        "description": f"If true, search across ALL Kubernetes clusters in the account instead of just the current cluster. Default is false (current cluster only). Use this when investigating cross-cluster issues or when the user asks about {subject} across their entire infrastructure.",
    }


TOOLS = [
    types.Tool(
        name="fetch_resource_issues_metadata",
        description="Fetch issues and alert metadata in a given time range. Can be filtered by namespace and/or specific Kubernetes resource. CLUSTER SCOPE: By default, queries the CURRENT cluster only. Set all_clusters=true to search across ALL clusters for issues affecting similar workloads. Use fetch_finding_by_id to get detailed information about a specific issue or alert.",
        inputSchema={
            "type": "object",
            "properties": {
                "start_datetime": _START_TIME_SCHEMA,
                "end_datetime": _END_TIME_SCHEMA,
                "limit": _LIMIT_SCHEMA,
                "namespace": {
                    "type": "string",
                    "description": "Filter by Kubernetes namespace (exact match).",
                },
                "workload": {
                    "type": "string",
                    "description": "Filter by Kubernetes resource name (e.g., Pod, Deployment, Job). Must be the full name. For Pods, include the exact generated suffix.",
                },
                "all_clusters": _all_clusters_schema("issues"),
                "clusters": _CLUSTERS_SCHEMA,
                "aggregation_key": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of event types to filter by (exact match). Not an exhaustive enum - pass any known value; leave empty to return all issue types. Common examples: PodOOMKilled, CrashLoopBackoff, FailedScheduling, PodLifecycleWarning, DeadlineExceeded, Unhealthy. Note some conditions have more than one possible value depending on the data source - e.g. a crash-looping pod may be recorded as CrashLoopBackoff (from a Kubernetes event) or KubePodCrashLooping (from a Prometheus alert); pass both if unsure which applies. Use this to narrow down a broad time range to a specific kind of event instead of browsing every issue in the window.",
                },
            },
            "required": ["start_datetime", "end_datetime"],
        },
    ),
    types.Tool(
        name="fetch_configuration_changes_metadata",
        description="Fetch configuration changes metadata in a given time range. Returns changes from Kubernetes clusters (deployments, configmaps, secrets, etc.) and external sources (e.g., LaunchDarkly feature flag changes). CLUSTER SCOPE: By default, queries the CURRENT cluster only and includes external changes. Set all_clusters=true to search across ALL clusters. Set include_external=false to exclude external (non-Kubernetes) changes. Can be filtered by namespace or specific workload name. Use fetch_finding_by_id to get detailed information about a specific change.",
        inputSchema={
            "type": "object",
            "properties": {
                "start_datetime": _START_TIME_SCHEMA,
                "end_datetime": _END_TIME_SCHEMA,
                "limit": _LIMIT_SCHEMA,
                "namespace": {
                    "type": "string",
                    "description": "Filter by Kubernetes namespace (exact match). Only applies to cluster changes, not external changes.",
                },
                "workload": {
                    "type": "string",
                    "description": "Filter by Kubernetes resource name (e.g., Pod, Deployment, Job). Must be the full name. For Pods, include the exact generated suffix. Only applies to cluster changes, not external changes.",
                },
                "include_external": {
                    "type": "boolean",
                    "description": "If true (default), include external configuration changes not associated with any Kubernetes cluster (e.g., LaunchDarkly feature flag changes, external system configurations). Set to false to only see Kubernetes cluster changes.",
                },
                "all_clusters": _all_clusters_schema("changes"),
                "clusters": _CLUSTERS_SCHEMA,
            },
            "required": ["start_datetime", "end_datetime"],
        },
    ),
    types.Tool(
        name="fetch_resource_recommendation",
        description="Fetch KRR (Kubernetes Resource Recommendations) for CPU and memory optimization. KRR provides AI-powered recommendations based on actual historical usage patterns for right-sizing workloads. Supports two usage modes: (1) Specific workload lookup - Use name_pattern with an exact name, namespace, and kind to get recommendations for a single workload. (2) Discovery mode - Use limit and sort_by to get a ranked list of top optimization opportunities. Optionally filter by namespace, name_pattern (wildcards supported), kind, or container. Returns current configured resources alongside recommended values. In discovery mode, results are sorted by potential savings. CLUSTER SCOPE: By default, queries the current cluster only. Set all_clusters=true to search across all clusters, or provide a specific list of cluster names in the 'clusters' parameter.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of recommendations to return (default: 10, max: 1000).",
                },
                "sort_by": {
                    "type": "string",
                    "description": "Field to sort recommendations by potential savings. Options: 'cpu_total' (default) - Total CPU savings (requests + limits), 'memory_total' - Total memory savings (requests + limits), 'cpu_requests' - CPU requests savings, 'memory_requests' - Memory requests savings, 'cpu_limits' - CPU limits savings, 'memory_limits' - Memory limits savings, 'priority' - Use scan priority field.",
                },
                "namespace": {
                    "type": "string",
                    "description": "Filter by Kubernetes namespace (exact match). Leave empty to search all namespaces.",
                },
                "name_pattern": {
                    "type": "string",
                    "description": "Filter by workload name pattern. Supports SQL LIKE patterns: Use '%' as wildcard (e.g., '%app%' matches any name containing 'app', 'prod-%' matches names starting with 'prod-'). Leave empty to match all names.",
                },
                "kind": {
                    "type": "string",
                    "description": "Filter by Kubernetes resource kind. Must be one of: Deployment, StatefulSet, DaemonSet, Job. Leave empty to include all kinds.",
                },
                "container": {
                    "type": "string",
                    "description": "Filter by container name (exact match). Leave empty to include all containers.",
                },
                "all_clusters": {
                    "type": "boolean",
                    "description": "If true, search across ALL clusters in the account instead of just the current cluster. Default is false (current cluster only). Use this when investigating cross-cluster issues or when the user asks about recommendations across their entire infrastructure.",
                },
                "clusters": _CLUSTERS_SCHEMA,
            },
            "required": [],
        },
    ),
]


def _parse_time(value: str) -> datetime:
    # isoparse, not datetime.fromisoformat: on Python 3.10 that rejects "Z",
    # "+00" and 4-digit fractions, all of which the rows and LLMs use.
    parsed = isoparse(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_rows(fixtures: Path, name: str) -> List[Dict[str, Any]]:
    path = fixtures / name
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _cluster_scope(args: Dict[str, Any], current_cluster: str) -> List[str]:
    """The clusters to query; ["*"] means every cluster in the account."""
    clusters = [c for c in (args.get("clusters") or []) if c]
    if clusters:
        return clusters
    if args.get("all_clusters"):
        return ["*"]
    return [current_cluster]


def _cluster_matches(cluster: str, clusters: List[str], include_external: bool) -> bool:
    if clusters == ["*"]:
        return include_external or cluster != EXTERNAL_CLUSTER
    return cluster in clusters or (include_external and cluster == EXTERNAL_CLUSTER)


def _query_findings(
    rows: List[Dict[str, Any]],
    args: Dict[str, Any],
    current_cluster: str,
    include_external: bool,
    columns: tuple,
) -> Any:
    if not args.get("start_datetime") or not args.get("end_datetime"):
        raise ValueError("start_datetime and end_datetime are required")
    start = _parse_time(args["start_datetime"])
    end = _parse_time(args["end_datetime"])
    clusters = _cluster_scope(args, current_cluster)
    aggregation_key = args.get("aggregation_key")
    if isinstance(aggregation_key, str):
        aggregation_key = [aggregation_key]
    # LLMs send [null] for "no filter".
    aggregation_key = [k for k in (aggregation_key or []) if k]
    limit = min(args.get("limit") or DEFAULT_LIMIT_CHANGE_ROWS, MAX_LIMIT_CHANGE_ROWS)

    matched = [
        row
        for row in rows
        if start <= _parse_time(row["creation_date"]) <= end
        and _cluster_matches(row["cluster"], clusters, include_external)
        and (not args.get("workload") or row["subject_name"] == args["workload"])
        and (not args.get("namespace") or row["subject_namespace"] == args["namespace"])
        and (not aggregation_key or row.get("aggregation_key") in aggregation_key)
    ]
    matched.sort(key=lambda row: _parse_time(row["starts_at"]), reverse=True)
    return [{c: row.get(c) for c in columns} for row in matched[:limit]]


def build_server(fixtures: Path, current_cluster: str) -> Server:
    server: Server = Server("robusta-platform-mcp-mock")

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        return TOOLS

    # The production server does not validate arguments against the schema.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, args: Dict[str, Any]) -> List[types.TextContent]:
        if name == "fetch_resource_issues_metadata":
            result = _query_findings(
                _load_rows(fixtures, "issues_metadata.json"),
                args,
                current_cluster,
                include_external=False,
                columns=ISSUE_COLUMNS + ("aggregation_key",),
            )
        elif name == "fetch_configuration_changes_metadata":
            include_external = args.get("include_external")
            result = _query_findings(
                _load_rows(fixtures, "changes_metadata.json"),
                args,
                current_cluster,
                include_external=True if include_external is None else include_external,
                columns=ISSUE_COLUMNS,
            )
        elif name == "fetch_resource_recommendation":
            return [
                types.TextContent(
                    type="text",
                    text=f"Could not find any recommendations with filters: {args}",
                )
            ]
        else:
            raise ValueError(f"Unknown tool: {name}")
        text = (
            json.dumps(result)
            if result
            else f"found no data matching the filters: {args}"
        )
        return [types.TextContent(type="text", text=text)]

    return server


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("--cluster", required=True)
    options = parser.parse_args()
    server = build_server(options.fixtures, options.cluster)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
