import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

CHANNEL_ID = "C08INC283X"
THREAD_TS = "1721003600.000000"
NODE = "ip-10-0-42-17.eu-west-1.compute.internal"

# ~45 realistic incident-response messages. None name the node or incident id.
_CHATTER = [
    ("U0ALICE", "ack — on it"),
    ("U0BOB", "seeing pods stuck ContainerCreating on it"),
    ("U0DAVE", "disk usage on the box is pegged"),
    ("U0ALICE", "looks like the image cache blew up again"),
    ("U0ERIN", "kubelet's been logging DiskPressure evictions"),
    ("U0BOB", "grafana shows root fs at 100% since ~12:30"),
    ("U0DAVE", "anyone know what filled it?"),
    ("U0ALICE", "probably stale containerd layers"),
    ("U0FRANK", "same thing happened last month"),
    ("U0ERIN", "should we drain it?"),
    ("U0BOB", "let's cordon first so nothing new lands there"),
    ("U0DAVE", "+1 cordon then investigate"),
    ("U0ALICE", "who has kubectl access to that cluster?"),
    ("U0FRANK", "I do, standing by"),
    ("U0ERIN", "anyone have the runbook link?"),
    ("U0BOB", "https://runbooks.internal/nodedisk"),
    ("U0DAVE", "runbook says clear the containerd cache"),
    ("U0ALICE", "careful not to remove running images"),
    ("U0FRANK", "crictl rmi --prune should be safe"),
    ("U0ERIN", "how many pods are affected?"),
    ("U0BOB", "~6 pending, 2 evicted"),
    ("U0DAVE", "any customer impact?"),
    ("U0ALICE", "checkout latency up a little, not paged"),
    ("U0FRANK", "ok will cordon shortly"),
    ("U0ERIN", "wait for confirmation before draining"),
    ("U0BOB", "agreed, cordon != drain"),
    ("U0DAVE", "it's been flapping NotReady too"),
    ("U0ALICE", "yeah saw that in the events"),
    ("U0FRANK", "the disk-pressure taint is set"),
    ("U0ERIN", "that's why the scheduler keeps failing"),
    ("U0BOB", "makes sense"),
    ("U0DAVE", "let's get holmes to help confirm"),
    ("U0ALICE", "good idea"),
    ("U0FRANK", "@oncall can someone loop in holmes"),
    ("U0ERIN", "doing it now"),
    ("U0BOB", "also file an incident ticket"),
    ("U0DAVE", "already did, linked above"),
    ("U0ALICE", "thanks"),
    ("U0FRANK", "standing by to cordon"),
    ("U0ERIN", "waiting on the go-ahead"),
    ("U0BOB", "let's have holmes confirm the target first"),
    ("U0DAVE", "sounds good"),
    ("U0ALICE", "ok"),
    ("U0FRANK", "ready when you are"),
    ("U0ERIN", "\U0001f44d"),
]


def _build_channel():
    base = 1721000000
    msgs = [{
        "type": "message", "ts": f"{base}.000000", "user": "U0ALERTMANAGER",
        "text": (":rotating_light: *NodeDiskError*: node "
                 f"`{NODE}` — root filesystem is 100% full, kubelet is reporting "
                 "DiskPressure and has started evicting pods. Incident id: INC-4F9K2."),
        "reply_count": 0,
    }]
    for i, (u, t) in enumerate(_CHATTER, start=1):
        msgs.append({"type": "message", "ts": f"{base + i * 30}.000000", "user": u, "text": t, "reply_count": 0})
    msgs.append({"type": "message", "ts": THREAD_TS, "user": "U0CAROL",
                 "text": "<@holmes> cordon the node", "reply_count": 0, "thread_ts": THREAD_TS})
    return msgs


_CHANNEL_MESSAGES = _build_channel()
_THREAD_REPLIES = []

# --- distractor cluster: ~30 nodes, ALL Ready (the disk problem is not visible
# from kubectl — the alert is the only source of truth for which node). The
# affected node is present but indistinguishable from the rest.
_NODES = [f"ip-10-0-{10 + i}-{(i * 7) % 90 + 5}.eu-west-1.compute.internal" for i in range(30)]
if NODE not in _NODES:
    _NODES[13] = NODE

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
        "you can tell whether it has a thread.\n\n"
        "Recovering missing context when answering inside a thread: a thread "
        "often does not contain everything you need — what the incident is, "
        "what changed, or what was already discussed in the channel. When the "
        "thread alone is not enough, read the surrounding channel with this "
        "tool: (1) messages just before the thread — set latest_ts to the "
        "thread's parent ts (thread_ts); (2) the start of the channel — page "
        "back with cursor toward the oldest messages to read how the incident "
        "began."
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
        "thread_ts is the ts of the thread's parent message. If the thread "
        "does not contain enough context to answer, read the surrounding "
        "channel with read_slack_channel_history_by_id on the same channel_id."
    ),
)
def read_slack_channel_thread_by_id(channel_id: str, thread_ts: str, inclusive: bool = True,
        latest_ts: Optional[str] = None, limit: int = 10, cursor: Optional[str] = None) -> str:
    if channel_id != CHANNEL_ID:
        return json.dumps({"ok": False, "error": "channel_not_found"})
    parent = next((m for m in _CHANNEL_MESSAGES if m["ts"] == thread_ts), None)
    if parent is None:
        return json.dumps({"ok": False, "error": "thread_not_found"})
    return json.dumps({"ok": True, "messages": [parent] + _THREAD_REPLIES, "has_more": False})


@mcp.tool(name="kubectl_get_nodes", description=(
        "List all Kubernetes nodes in the cluster with their Ready status "
        "(kubectl get nodes)."))
def kubectl_get_nodes() -> str:
    # All nodes report Ready — the disk problem is not visible here.
    return json.dumps({"nodes": [{"name": n, "status": "Ready"} for n in _NODES]})


@mcp.tool(name="kubectl_get_events", description=(
        "List recent Kubernetes events in a namespace (kubectl get events)."))
def kubectl_get_events(namespace: str = "default") -> str:
    return json.dumps({"events": [
        {"type": "Warning", "reason": "FailedScheduling", "message": "0/30 nodes are available: 1 node(s) had untolerated taint."},
        {"type": "Normal", "reason": "Pulling", "message": "Pulling image \"checkout:1.4.2\""},
        {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container"},
    ]})


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
