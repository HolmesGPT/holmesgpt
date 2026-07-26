"""Shared stub MCP server for the Slack channel-history evals (283 positive,
284 negative). Both fixtures load THIS file, so the tool descriptions — which
mirror the relay production prompt — cannot diverge between the two evals.

The read_slack_channel_history_by_id description below is the EXACT one-line
guidance added to relay (relay/pkg/apps/mcp/tools/slack.py). Keep them in sync.
"""

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

CHANNEL_ID = "C08INC283X"
THREAD_TS = "1721000180.000000"
NODE = "ip-10-0-42-17.eu-west-1.compute.internal"

# Short incident channel: the originating alert is the oldest message, followed
# by a handful of messages, then the thread the user is talking to Holmes in.
# Short on purpose so a single read_slack_channel_history_by_id call returns the
# whole channel (including the alert) — the eval measures WHETHER Holmes reads
# the channel, not how deep it pages.
_CHANNEL = [
    ("U0ALERTMANAGER", ":rotating_light: *NodeDiskError*: node `" + NODE + "` — "
     "root filesystem is 100% full, kubelet is reporting DiskPressure and has "
     "started evicting pods."),
    ("U0ALICE", "ack — taking a look"),
    ("U0BOB", "pods are stuck ContainerCreating on it"),
    ("U0ALICE", "let's cordon it first so nothing new lands there"),
    ("U0BOB", "agreed, get holmes to confirm the target"),
    ("U0CAROL", "<@holmes> cordon the node"),  # thread parent (THREAD_TS)
]


def _build_channel():
    base = 1721000000
    msgs = []
    for i, (u, t) in enumerate(_CHANNEL):
        ts = THREAD_TS if i == len(_CHANNEL) - 1 else f"{base + i * 30}.000000"
        m = {"type": "message", "ts": ts, "user": u, "text": t, "reply_count": 0}
        if i == len(_CHANNEL) - 1:
            m["thread_ts"] = THREAD_TS
        msgs.append(m)
    return msgs


_CHANNEL_MESSAGES = _build_channel()

# Noisy but realistic cluster: several NotReady nodes, and TWO with DiskPressure
# (including the incident node). So kubectl alone cannot say which node THIS
# incident is about — that fact lives only in the Slack alert.
_READY = [f"ip-10-0-{10 + i}-{(i * 7) % 90 + 5}.eu-west-1.compute.internal" for i in range(20)]
_NODES = [{"name": n, "status": "Ready", "condition": "KubeletReady"} for n in _READY if n != NODE]
_NODES += [
    {"name": NODE, "status": "NotReady", "condition": "DiskPressure"},
    {"name": "ip-10-0-55-23.eu-west-1.compute.internal", "status": "NotReady", "condition": "DiskPressure"},
    {"name": "ip-10-0-31-9.eu-west-1.compute.internal", "status": "NotReady", "condition": "MemoryPressure"},
]

mcp = FastMCP("robusta-platform-mcp-stub")


def _parse_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        return int(cursor.split(":", 1)[1]) if ":" in cursor else int(cursor)
    except (ValueError, IndexError):
        return 0


@mcp.tool(name="read_slack_channel_history_by_id", description=(
        "Read a page of messages from a Slack channel, newest first, going "
        "backwards in time from latest_ts (or from now if omitted). Wraps the "
        "Slack conversations.history API; each message includes reply_count so "
        "you can tell whether it has a thread. If you are answering in a thread "
        "that does not already name what you need (for example, which resource "
        "an alert refers to), read this channel first — before running other "
        "tools or asking the user — to recover it from the earlier messages."
    ),
)
def read_slack_channel_history_by_id(channel_id: str, latest_ts: Optional[str] = None,
        inclusive: bool = True, limit: int = 10, cursor: Optional[str] = None) -> str:
    if channel_id != CHANNEL_ID:
        return json.dumps({"ok": False, "error": "channel_not_found"})
    limit = min(int(limit), 999)
    msgs = sorted(_CHANNEL_MESSAGES, key=lambda m: float(m["ts"]), reverse=True)
    if latest_ts:
        latest = float(latest_ts)
        msgs = [m for m in msgs if (float(m["ts"]) <= latest if inclusive else float(m["ts"]) < latest)]
    offset = _parse_cursor(cursor)
    window = msgs[offset:offset + limit]
    has_more = offset + limit < len(msgs)
    resp = {"ok": True, "messages": window, "has_more": has_more}
    if has_more:
        resp["response_metadata"] = {"next_cursor": f"offset:{offset + limit}"}
    return json.dumps(resp)


@mcp.tool(name="read_slack_channel_thread_by_id", description=(
        "Read the replies in a Slack thread (Slack conversations.replies). "
        "thread_ts is the ts of the thread's parent message."
    ),
)
def read_slack_channel_thread_by_id(channel_id: str, thread_ts: str, inclusive: bool = True,
        latest_ts: Optional[str] = None, limit: int = 10, cursor: Optional[str] = None) -> str:
    if channel_id != CHANNEL_ID:
        return json.dumps({"ok": False, "error": "channel_not_found"})
    parent = next((m for m in _CHANNEL_MESSAGES if m["ts"] == thread_ts), None)
    if parent is None:
        return json.dumps({"ok": False, "error": "thread_not_found"})
    return json.dumps({"ok": True, "messages": [parent], "has_more": False})


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
