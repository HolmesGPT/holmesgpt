"""Local stdio MCP server that mocks the cross-cluster remote-tools surface
(ROB-310) — the SELF-CALL variant (eval 274).

Reproduces the failure seen in production conversation
8ddf4178-93dd-4093-82d5-f33c97d5da66: asked to list pods from ALL clusters,
the caller Holmes fanned out and called the *remote* tool with its OWN cluster
as agent_name (a value never in the enum), which relay rejected.

Unlike 271's mock (which hard-codes the three remote agents as a Literal enum,
so FastMCP rejects an own-cluster value before the handler runs), this mock
mirrors relay's *runtime* surface: agent_name is a free string validated in the
handler, and an own-cluster value returns the same crisp, self-correcting error
relay now returns — so the eval exercises the model's self-call recovery.

Simulated fleet: own cluster prod-us-east (local tool) + remote agents
prod-eu-west / prod-ap-south / staging-core (remote tool).
"""

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Fleet Diagnostics Service")

OWN_CLUSTER = "prod-us-east"
REMOTE_AGENTS = ["prod-eu-west", "prod-ap-south", "staging-core"]

_RECORDS = {
    "prod-us-east": "RTC-EVAL-USEAST-c4k7n2",
    "prod-eu-west": "RTC-EVAL-EUWEST-9p3q8d",
    "prod-ap-south": "RTC-EVAL-APSOUTH-x6m1v5",
    "staging-core": "RTC-EVAL-STGCORE-t2w9z4",
}

_REMOTE_DESC = (
    "Run the 'fetch_cluster_diagnostics' tool on another agent/cluster. You "
    f"are running in cluster '{OWN_CLUSTER}', which is NOT a valid agent_name "
    f"— NEVER pass '{OWN_CLUSTER}' here; use your local "
    "'fetch_cluster_diagnostics' tool for your own cluster instead. When asked "
    "about ALL clusters/agents, call this tool once for each of "
    f"{REMOTE_AGENTS} AND ALSO run the local 'fetch_cluster_diagnostics' for "
    f"'{OWN_CLUSTER}' (NOT the remote tool), then aggregate the results.\n\n"
    "Args:\n"
    "    agent_name: The agent (Holmes instance) to run this tool on. "
    f"agent_name and cluster_name are synonyms. Must be one of {REMOTE_AGENTS}."
)


def _record(cluster: str) -> str:
    return json.dumps(
        {
            "cluster": cluster,
            "verification_code": _RECORDS[cluster],
            "status": "healthy",
        }
    )


@mcp.tool(
    description=(
        "Fetch this cluster's diagnostics record (includes the cluster's "
        "verification_code) from the in-cluster info service."
    )
)
def fetch_cluster_diagnostics() -> str:
    return _record(OWN_CLUSTER)


@mcp.tool(description=_REMOTE_DESC)
def remote_fetch_cluster_diagnostics(agent_name: str) -> str:
    # Mirror relay's handler validation. Own-cluster -> crisp self-correcting
    # error pointing at the local tool (this is the fix under test).
    if agent_name == OWN_CLUSTER:
        return (
            f"ERROR: agent '{agent_name}' is your OWN cluster — the remote "
            "tool only runs on OTHER clusters. Use your local "
            "'fetch_cluster_diagnostics' tool for it; do not call "
            "'remote_fetch_cluster_diagnostics' for your own cluster."
        )
    if agent_name not in _RECORDS:
        return (
            f"ERROR: agent '{agent_name}' is not a valid agent_name "
            f"(valid agents: {REMOTE_AGENTS})"
        )
    return _record(agent_name)


if __name__ == "__main__":
    mcp.run()
