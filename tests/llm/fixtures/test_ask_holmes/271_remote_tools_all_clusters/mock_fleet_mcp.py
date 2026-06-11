"""Local stdio MCP server that mocks the cross-cluster remote-tools surface
(ROB-310) for a SINGLE Holmes instance.

`remote_fetch_cluster_diagnostics` looks exactly like the dynamic
`remote_<tool>` tools that relay's platform-mcp builds — a required
`agent_name` enum plus the caller-aware steering text in the description —
but runs locally with canned per-agent responses, so evals 271/272/273 can
test the LLM's local-vs-remote routing without a live multi-instance stack.

Simulated fleet: own cluster prod-us-east (local tool) + remote agents
prod-eu-west / prod-ap-south / staging-core (remote tool). Verification
codes are unique per cluster and only discoverable by calling the tools.
"""

import json
from typing import Literal

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Fleet Diagnostics Service")

OWN_CLUSTER = "prod-us-east"

_RECORDS = {
    "prod-us-east": "RTC-EVAL-USEAST-c4k7n2",
    "prod-eu-west": "RTC-EVAL-EUWEST-9p3q8d",
    "prod-ap-south": "RTC-EVAL-APSOUTH-x6m1v5",
    "staging-core": "RTC-EVAL-STGCORE-t2w9z4",
}


def _record(cluster: str) -> str:
    return json.dumps(
        {
            "cluster": cluster,
            "verification_code": _RECORDS[cluster],
            "status": "healthy",
        }
    )


@mcp.tool()
def fetch_cluster_diagnostics() -> str:
    """Fetch this cluster's diagnostics record (includes the cluster's
    verification_code) from the in-cluster info service."""
    return _record(OWN_CLUSTER)


@mcp.tool()
def remote_fetch_cluster_diagnostics(
    agent_name: Literal["prod-eu-west", "prod-ap-south", "staging-core"],
) -> str:
    """Run the 'fetch_cluster_diagnostics' tool on another agent/cluster.
    You are running in cluster 'prod-us-east' — it is NOT in the agent_name
    enum; for your own cluster always use your local
    'fetch_cluster_diagnostics' tool instead of this one. When asked about
    ALL clusters/agents, call this tool once per agent_name in the enum AND
    ALSO run the local 'fetch_cluster_diagnostics' for your own cluster,
    then aggregate the results. When asked about one specific remote
    cluster, call this tool once with that cluster as agent_name. Fetch the
    cluster's diagnostics record (includes the cluster's verification_code)
    from the in-cluster info service.

    Args:
        agent_name: The agent (Holmes instance) to run this tool on.
            agent_name and cluster_name are synonyms — pass the target
            cluster's name. Your own cluster is deliberately absent from
            this enum: run the tool locally for it.
    """
    if agent_name == OWN_CLUSTER or agent_name not in _RECORDS:
        return f"ERROR: agent '{agent_name}' is not a valid agent_name"
    return _record(agent_name)


if __name__ == "__main__":
    mcp.run()
