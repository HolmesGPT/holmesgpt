"""Deterministic stub of relay's Slack platform-mcp tools (+ a mock cordon action).

Stand-in for the `robusta-platform-mcp` server. Reproduces a real production
failure: in a NEW conversation a user asks Holmes to ACT ("@holmes cordon the
node"), but the node's name is only in the channel's opening NodeDiskError
alert — not in the (empty) thread. If Holmes runs the channel-history tool it
recovers the node and can cordon it; in prod it instead replied "you didn't
specify a node".

Tools exposed:
  - read_slack_channel_history_by_id / read_slack_channel_thread_by_id
      mirror the real relay Slack read tools.
  - cordon_node
      a mock action so Holmes CAN act once it knows the node. This removes the
      "I can't perform write actions" deflection and isolates the real signal:
      did Holmes read the channel to recover the node, or bounce the request
      back to the user?

The node `ip-10-0-42-17.eu-west-1.compute.internal` appears ONLY in the opening
alert; the rest of the channel says "that node" / "it".
"""

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

CHANNEL_ID = "C08INC283X"
THREAD_TS = "1721003600.000000"

# Channel, oldest first. The OPENING message is the NodeDiskError alert — the
# only place the affected node is named. Everything after refers to it as "that
# node" / "it".
_CHANNEL_MESSAGES = [
    {
        "type": "message",
        "ts": "1721000000.000000",  # channel's FIRST message: the alert
        "user": "U0ALERTMANAGER",
        "text": (
            ":rotating_light: *NodeDiskError*: node "
            "`ip-10-0-42-17.eu-west-1.compute.internal` — root filesystem is "
            "100% full, kubelet is reporting DiskPressure and has started "
            "evicting pods. Incident id: INC-4F9K2."
        ),
        "reply_count": 0,
    },
    {"type": "message", "ts": "1721000600.000000", "user": "U0ALICE",
     "text": "yeah that node has been flapping NotReady for a while", "reply_count": 0},
    {"type": "message", "ts": "1721001200.000000", "user": "U0BOB",
     "text": "pods scheduled onto it are stuck ContainerCreating", "reply_count": 0},
    {"type": "message", "ts": "1721001800.000000", "user": "U0ALICE",
     "text": "someone should cordon it before more workloads land there", "reply_count": 0},
    {
        "type": "message",
        "ts": THREAD_TS,
        "user": "U0CAROL",
        "text": "<@holmes> cordon the node",
        "reply_count": 0,
        "thread_ts": THREAD_TS,
    },
]

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
