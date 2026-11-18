"""Stub cluster tools for the OpenUI visualization evals (301-305).

The model must pull every value from a tool result, so the evals need data
tools with a stable, known shape. These two serve the same fixtures the
frontend's Storybook renders (robusta-frontend:
src/features/design-system/stories/gen-ui/openui.ts): 228 pods across three
namespaces and three phases, and a two-series CPU matrix. No cluster, no
Prometheus; the cluster toolsets are disabled in each case's toolsets.yaml.
"""

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("cluster-tools-openui")

_NAMESPACES = ["payments", "auth", "default"]
_PODS = (
    [
        {
            "metadata": {"name": f"app-{i}", "namespace": _NAMESPACES[i % 3]},
            "status": {"phase": "Running"},
        }
        for i in range(212)
    ]
    + [
        {
            "metadata": {"name": f"queued-{i}", "namespace": "default"},
            "status": {"phase": "Pending"},
        }
        for i in range(12)
    ]
    + [
        {
            "metadata": {"name": f"crashed-{i}", "namespace": "payments"},
            "status": {"phase": "Failed"},
        }
        for i in range(4)
    ]
)

# 5-minute samples from 2026-10-30T08:00Z, CPU in cores.
_T0 = 1793347200
_CPU = {
    ("api-gateway", "payments"): [42, 45, 44, 51, 63, 71, 68, 59, 55, 52, 49, 47],
    ("billing", "auth"): [30, 31, 29, 34, 36, 38, 41, 44, 40, 37, 35, 33],
}


@mcp.tool(
    name="kubectl_get_pods",
    description=(
        "List the pods in the cluster as JSON, like `kubectl get pods -A -o json`: "
        "{items: [{metadata: {name, namespace}, status: {phase}}]}. "
        "Pass a namespace to list one namespace, or leave it empty for all."
    ),
)
def kubectl_get_pods(namespace: str = "") -> str:
    items = [
        p for p in _PODS if not namespace or p["metadata"]["namespace"] == namespace
    ]
    return json.dumps({"items": items})


@mcp.tool(
    name="execute_prometheus_range_query",
    description=(
        "Run a PromQL range query over the last hour and return the Prometheus HTTP API JSON: "
        "{status, data: {resultType: 'matrix', result: [{metric: {pod, namespace}, values: [[unix_seconds, 'value'], ...]}]}}. "
        "Example: sum by (pod, namespace) (rate(container_cpu_usage_seconds_total[5m]))."
    ),
)
def execute_prometheus_range_query(
    query: str, start: str = "", end: str = "", step: str = "300"
) -> str:
    result = [
        {
            "metric": {"pod": pod, "namespace": namespace},
            "values": [[_T0 + i * 300, str(v / 100)] for i, v in enumerate(values)],
        }
        for (pod, namespace), values in _CPU.items()
    ]
    return json.dumps(
        {"status": "success", "data": {"resultType": "matrix", "result": result}}
    )


if __name__ == "__main__":
    mcp.run()
