"""Deterministic stub of relay's Slack platform-mcp tools (+ a mock cordon action).

Stand-in for the `robusta-platform-mcp` server. Reproduces a real production
failure: in a NEW conversation a user asks Holmes to ACT ("@holmes cordon the
node"), but the affected node is named only in the channel's OPENING
NodeDiskError alert — and the channel has ~45 messages of incident chatter on
top of it, so a naive "read the latest messages" call does NOT return the alert.
Holmes has to read back to the START of the channel to recover the node. In prod
it instead replied "you didn't specify a node".

Tools exposed:
  - read_slack_channel_history_by_id / read_slack_channel_thread_by_id
      mirror the real relay Slack read tools (newest-first, latest_ts + cursor
      paging, limit default 10 / max 999).
  - cordon_node
      a mock action so Holmes CAN act once it knows the node — removes the "I
      can't perform write actions" deflection and isolates the real signal: did
      Holmes read the channel (back to the alert) to recover the node, or bounce
      the request back to the user?

The node `ip-10-0-42-17.eu-west-1.compute.internal` and incident id `INC-4F9K2`
appear ONLY in the opening alert (message 1 of ~47). Every other message refers
to it as "the node" / "it" / "the box", so the name cannot be guessed and is not
in the recent (default newest-10) window.
"""

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

CHANNEL_ID = "C08INC283X"
THREAD_TS = "1721003600.000000"
NODE = "ip-10-0-42-17.eu-west-1.compute.internal"

# ~45 realistic incident-response messages. None name the node or incident id —
# they refer to "the node" / "it" / "the box" — so those facts live only in the
# opening alert.
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
    ("U0ERIN", "👍"),
]


def _build_channel() -> list:
    base = 1721000000
    msgs = [
        {
            "type": "message",
            "ts": f"{base}.000000",  # OLDEST: the alert — the only source of the node name
            "user": "U0ALERTMANAGER",
            "text": (
                ":rotating_light: *NodeDiskError*: node "
                f"`{NODE}` — root filesystem is 100% full, kubelet is reporting "
                "DiskPressure and has started evicting pods. Incident id: INC-4F9K2."
            ),
            "reply_count": 0,
        }
    ]
    for i, (user, text) in enumerate(_CHATTER, start=1):
        msgs.append(
            {
                "type": "message",
                "ts": f"{base + i * 30}.000000",
                "user": user,
                "text": text,
                "reply_count": 0,
            }
        )
    # NEWEST: the cordon request (the new conversation / thread parent)
    msgs.append(
        {
            "type": "message",
            "ts": THREAD_TS,
            "user": "U0CAROL",
            "text": "<@holmes> cordon the node",
            "reply_count": 0,
            "thread_ts": THREAD_TS,
        }
    )
    return msgs


_CHANNEL_MESSAGES = _build_channel()
_THREAD_REPLIES = []

mcp = FastMCP("robusta-platform-mcp-stub")


def _parse_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        return int(cursor.split(":", 1)[1]) if ":" in cursor else int(cursor)
    except (ValueError, IndexError):
        return 0


@mcp.tool(
    name="read_slack_channel_history_by_id",
    description=(
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
def read_slack_channel_history_by_id(
    channel_id: str,
    latest_ts: Optional[str] = None,
    inclusive: bool = True,
    limit: int = 10,
    cursor: Optional[str] = None,
) -> str:
    if channel_id != CHANNEL_ID:
        return json.dumps({"ok": False, "error": "channel_not_found"})
    limit = min(int(limit), 999)
    msgs = sorted(_CHANNEL_MESSAGES, key=lambda m: float(m["ts"]), reverse=True)
    if latest_ts:
        latest = float(latest_ts)
        msgs = [
            m
            for m in msgs
            if (float(m["ts"]) <= latest if inclusive else float(m["ts"]) < latest)
        ]
    offset = _parse_cursor(cursor)
    window = msgs[offset : offset + limit]
    has_more = offset + limit < len(msgs)
    response = {"ok": True, "messages": window, "has_more": has_more}
    if has_more:
        response["response_metadata"] = {"next_cursor": f"offset:{offset + limit}"}
    return json.dumps(response)


@mcp.tool(
    name="read_slack_channel_thread_by_id",
    description=(
        "Read the replies in a Slack thread (Slack conversations.replies). "
        "thread_ts is the ts of the thread's parent message. If the thread "
        "does not contain enough context to answer, read the surrounding "
        "channel with read_slack_channel_history_by_id on the same channel_id."
    ),
)
def read_slack_channel_thread_by_id(
    channel_id: str,
    thread_ts: str,
    inclusive: bool = True,
    latest_ts: Optional[str] = None,
    limit: int = 10,
    cursor: Optional[str] = None,
) -> str:
    if channel_id != CHANNEL_ID:
        return json.dumps({"ok": False, "error": "channel_not_found"})
    parent = next((m for m in _CHANNEL_MESSAGES if m["ts"] == thread_ts), None)
    if parent is None:
        return json.dumps({"ok": False, "error": "thread_not_found"})
    messages = [parent] + _THREAD_REPLIES
    return json.dumps({"ok": True, "messages": messages, "has_more": False})


@mcp.tool(
    name="cordon_node",
    description=(
        "Cordon a Kubernetes node so the scheduler places no new pods on it "
        "(kubectl cordon). Requires the exact node name. Use this once you know "
        "which node to cordon."
    ),
)
def cordon_node(node_name: str) -> str:
    node_name = (node_name or "").strip()
    if not node_name:
        return json.dumps({"ok": False, "error": "node_name is required"})
    return json.dumps({"ok": True, "cordoned": node_name})


if __name__ == "__main__":
    mcp.run()
