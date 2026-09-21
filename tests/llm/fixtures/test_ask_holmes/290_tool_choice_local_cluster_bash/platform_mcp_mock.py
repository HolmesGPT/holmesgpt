"""Mock Robusta platform MCP for the tool-choice evals (290/291/292).

Mirrors relay's platform-mcp: `remote_*` tools that the PLATFORM dispatches to
another Holmes instance in the fleet, selected by `agent_name`. Distinct from
the kubernetes MCP server, which talks to remote clusters directly over
kubeconfig contexts. Holmes must pick these only for fleet-wide questions.
"""

import json

from mcp.server.fastmcp import FastMCP

AGENTS = ["eu-prod-1", "us-prod-2", "ap-staging-3"]

mcp = FastMCP("platform-mcp-mock")


@mcp.tool(name="list_available_clusters", description=(
        "List every cluster in the Robusta fleet that this account can reach, "
        "along with whether each is currently connected. Returns the "
        "`agent_name` values accepted by the remote_* tools."))
def list_available_clusters() -> str:
    return json.dumps({
        "clusters": [
            {"agent_name": "eu-prod-1", "connected": True},
            {"agent_name": "us-prod-2", "connected": True},
            {"agent_name": "ap-staging-3", "connected": False},
        ]
    })


@mcp.tool(name="remote_kubernetes_tabular_query", description=(
        "Run a read-only Kubernetes query on ANOTHER cluster in the Robusta "
        "fleet, routed through the platform. Requires `agent_name` naming the "
        "target cluster (see list_available_clusters)."))
def remote_kubernetes_tabular_query(agent_name: str, query: str) -> str:
    if agent_name not in AGENTS:
        return json.dumps({"error": f"unknown agent_name {agent_name!r}; known: {AGENTS}"})
    if agent_name == "ap-staging-3":
        return json.dumps({"error": "cluster ap-staging-3 is not connected"})
    return json.dumps({
        "agent_name": agent_name,
        "query": query,
        "rows": [{"namespace": "payments", "deployment": "checkout-api", "ready": "2/3"}],
    })


if __name__ == "__main__":
    mcp.run()
