"""Mock kubernetes-mcp-server for the tool-choice evals (290/291/292).

Mirrors the real kubernetes-mcp-server toolset: it is wired to REMOTE clusters
via kubeconfig contexts, so every tool takes an explicit `context`. Holmes must
pick these tools only when the question is about another cluster — never for
the cluster Holmes itself runs on.

Self-contained so the eval needs no live cluster.
"""

import json

from mcp.server.fastmcp import FastMCP

CONTEXTS = ["eu-prod-1", "us-prod-2"]

_PODS = {
    "eu-prod-1": [
        {"name": "checkout-api-7d9f-abc", "namespace": "payments", "status": "CrashLoopBackOff", "restarts": 47},
        {"name": "ledger-6b8c-def", "namespace": "payments", "status": "Running", "restarts": 0},
    ],
    "us-prod-2": [
        {"name": "search-indexer-5f7a-ghi", "namespace": "search", "status": "Running", "restarts": 1},
    ],
}

mcp = FastMCP("kubernetes-mcp-mock")


@mcp.tool(name="configuration_contexts_list", description=(
        "List every Kubernetes cluster context available to this MCP server. "
        "Call this first to discover which remote clusters can be queried, then "
        "pass one of the returned names as the `context` argument of any other "
        "tool. Does NOT cover the cluster Holmes itself runs on."))
def configuration_contexts_list() -> str:
    return json.dumps({"contexts": CONTEXTS, "default": CONTEXTS[0]})


@mcp.tool(name="pods_list_in_namespace", description=(
        "List pods in a namespace on a REMOTE Kubernetes cluster. Requires the "
        "`context` argument naming the target cluster (see "
        "configuration_contexts_list)."))
def pods_list_in_namespace(context: str, namespace: str) -> str:
    if context not in CONTEXTS:
        return json.dumps({"error": f"unknown context {context!r}; known: {CONTEXTS}"})
    pods = [p for p in _PODS.get(context, []) if p["namespace"] == namespace]
    return json.dumps({"context": context, "namespace": namespace, "pods": pods})


if __name__ == "__main__":
    mcp.run()
