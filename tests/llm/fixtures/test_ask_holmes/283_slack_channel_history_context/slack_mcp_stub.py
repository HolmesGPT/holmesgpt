"""Deterministic stub of relay's Slack platform-mcp read tools.

This is a stand-in for the `robusta-platform-mcp` server that relay exposes to
Holmes. It serves a fixed incident-channel timeline so the eval can verify that,
when the conversation (thread) is missing the critical details, Holmes calls
`read_slack_channel_history_by_id` to recover them from the surrounding channel.

The two tools mirror the real relay tools in
`relay/relay/pkg/apps/mcp/tools/slack.py` — same names, same key parameters
(channel_id / thread_ts / latest_ts / inclusive / limit / cursor), same
"missing context" guidance in the descriptions, and the same "return Slack's
response unmodified" shape (conversations.history / conversations.replies).

The critical facts — the affected node `ip-10-0-42-17.eu-west-1.compute.internal`,
the incident id `INC-4F9K2`, and the DiskPressure / full-root-volume root cause —
live ONLY in the channel's opening alert. The conversation (thread) never names
the node, the incident id, or the issue, so the eval cannot be answered from the
conversation alone: Holmes must read the channel to even know what to investigate.

Run from the holmesgpt repo root (the eval launches it as a stdio subprocess):
    python tests/llm/fixtures/test_ask_holmes/283_slack_channel_history_context/slack_mcp_stub.py
"""

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

CHANNEL_ID = "C08INC283X"
THREAD_TS = "1721003600.000000"

# The incident channel, oldest first. The channel OPENS with the AlertManager
# alert that carries the only copy of the critical details (node name, incident
# id, root cause). The thread the user is chatting in (THREAD_TS, the last
# message) names none of them — they can only be recovered by reading the channel.
_CHANNEL_MESSAGES = [
    {
        "type": "message",
        "ts": "1721000000.000000",  # channel's FIRST message: the incident alert
        "user": "U0ALERTMANAGER",
        "text": (
            ":rotating_light: *AlertManager* — *KubeNodeNotReady*: node "
            "`ip-10-0-42-17.eu-west-1.compute.internal` has been NotReady for "
            "12m. Incident id: INC-4F9K2. kubelet is reporting *DiskPressure* — "
            "the node's root volume is 98% full."
        ),
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": "1721000600.000000",
        "user": "U0ALICE",
        "text": (
            "confirmed — /var/lib/containerd on that node filled up with stale "
            "image layers, kubelet started evicting pods"
        ),
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": "1721001800.000000",
        "user": "U0BOB",
        "text": "new pods scheduled there are stuck ContainerCreating",
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": "1721003000.000000",
        "user": "U0ALICE",
        "text": "cordoning it now while we free up disk",
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": THREAD_TS,  # the parent message of the thread the user is in
        "user": "U0BOB",
        "text": (
            "<@holmes> can you investigate this incident and tell us what's "
            "actually wrong and the root cause?"
        ),
        "reply_count": 2,
        "reply_users_count": 2,
        "thread_ts": THREAD_TS,
    },
]

# Replies inside the THREAD_TS thread. Note: still no node name, incident id, or
# issue — that context only exists earlier in the channel (the opening alert).
_THREAD_REPLIES = [
    {
        "type": "message",
        "ts": "1721003605.000000",
        "user": "U0HOLMES",
        "text": "On it — investigating the incident now.",
        "thread_ts": THREAD_TS,
    },
    {
        "type": "message",
        "ts": "1721003700.000000",
        "user": "U0BOB",
        "text": "we need the specific node and what's wrong with it.",
        "thread_ts": THREAD_TS,
    },
]

_CHANNEL_ID_DESC = (
    "Conversation ID of the channel to read (e.g. C0123456789). The bot must "
    "be a member of the conversation."
)

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

    # Slack returns newest-first.
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


if __name__ == "__main__":
    mcp.run()
