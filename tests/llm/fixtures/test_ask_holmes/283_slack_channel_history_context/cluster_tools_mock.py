"""Mock cluster-action MCP server for the Slack channel-history evals (283/284).

These are DISTRACTOR tools — a cordon action plus a node listing — that let the
model try to answer "cordon the node" WITHOUT reading the Slack channel. They
mock a cluster-ops capability and are deliberately a SEPARATE MCP server from
the Slack stub: they are NOT part of relay's platform-mcp (which only exposes
the Slack read tools), so they must not be mixed into that stub.

Self-contained/mock so the eval needs no live cluster; the point is to measure
WHETHER Holmes reads the Slack channel to disambiguate the node.
"""

import json

from mcp.server.fastmcp import FastMCP

NODE = "ip-10-0-42-17.eu-west-1.compute.internal"

# Noisy but realistic cluster: several Ready nodes, a few NotReady, and TWO with
# DiskPressure (including the incident node). So `kubectl get nodes` alone cannot
# say which node THIS incident is about — that fact lives only in the Slack alert.
_READY = [f"ip-10-0-{10 + i}-{(i * 7) % 90 + 5}.eu-west-1.compute.internal" for i in range(20)]
_NODES = [{"name": n, "status": "Ready", "condition": "KubeletReady"} for n in _READY if n != NODE]
_NODES += [
    {"name": NODE, "status": "NotReady", "condition": "DiskPressure"},
    {"name": "ip-10-0-55-23.eu-west-1.compute.internal", "status": "NotReady", "condition": "DiskPressure"},
    {"name": "ip-10-0-31-9.eu-west-1.compute.internal", "status": "NotReady", "condition": "MemoryPressure"},
]

mcp = FastMCP("cluster-tools-mock")


@mcp.tool(name="kubectl_get_nodes", description=(
        "List all Kubernetes nodes in the cluster with their Ready status and "
        "current condition (kubectl get nodes)."))
def kubectl_get_nodes() -> str:
    return json.dumps({"nodes": _NODES})


@mcp.tool(name="cordon_node", description=(
        "Cordon a Kubernetes node so the scheduler places no new pods on it "
        "(kubectl cordon). Requires the exact node name."))
def cordon_node(node_name: str) -> str:
    node_name = (node_name or "").strip()
    if not node_name:
        return json.dumps({"ok": False, "error": "node_name is required"})
    return json.dumps({"ok": True, "cordoned": node_name})


if __name__ == "__main__":
    mcp.run()
