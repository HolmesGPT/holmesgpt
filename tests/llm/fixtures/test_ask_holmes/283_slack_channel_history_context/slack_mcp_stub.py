"""Deterministic stub of relay's Slack platform-mcp read tools.

This is a stand-in for the `robusta-platform-mcp` server that relay exposes to
Holmes. It serves a fixed incident-channel timeline so the eval can verify that,
when a thread is missing context, Holmes calls `read_slack_channel_history_by_id`
to recover it from the surrounding channel.

The two tools mirror the real relay tools in
`relay/relay/pkg/apps/mcp/tools/slack.py` — same names, same key parameters
(channel_id / thread_ts / latest_ts / inclusive / limit / cursor), same
"missing context" guidance in the descriptions, and the same "return Slack's
response unmodified" shape (conversations.history / conversations.replies).

The discriminating facts (release `checkout-api v4.19.2` and deploy id
`DPL-7Q2X`) live ONLY in the channel history, never in the thread, so the eval
cannot be passed by hallucinating from the thread alone.

Run from the holmesgpt repo root (the eval launches it as a stdio subprocess):
    python tests/llm/fixtures/test_ask_holmes/283_slack_channel_history_context/slack_mcp_stub.py
"""

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

CHANNEL_ID = "C08INC283X"
THREAD_TS = "1721003600.000000"

# The incident channel, oldest first. The channel opens with the incident
# alert; the deploy that introduced the incident is announced right after.
# The thread the user is asking in (THREAD_TS, the last message) contains none
# of these facts, so they can only be recovered by reading the channel.
_CHANNEL_MESSAGES = [
    {
        "type": "message",
        "ts": "1721000000.000000",  # channel's FIRST message: the incident alert
        "user": "U0PAGERDUTY",
        "text": (
            ":fire: *PagerDuty* :fire: Incident opened: *checkout-api* is "
            "returning elevated 5xx errors in prod. First seen 12:47 UTC. "
            "Owning team: payments."
        ),
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": "1721000600.000000",  # the key fact: which release + deploy id
        "user": "U0DEPLOYBOT",
        "text": (
            "Deploy pipeline: *checkout-api v4.19.2* rolled out to prod at "
            "12:45 UTC (deploy-id DPL-7Q2X). Author: r.mehta."
        ),
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": "1721001800.000000",
        "user": "U0ALICE",
        "text": "who pushed to checkout-api right before this started?",
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": "1721002400.000000",
        "user": "U0BOB",
        "text": "error rate on /checkout jumped to ~40% right after 12:45",
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": "1721003000.000000",
        "user": "U0ALICE",
        "text": "starting a rollback of checkout-api now",
        "reply_count": 0,
    },
    {
        "type": "message",
        "ts": THREAD_TS,  # the parent message of the thread the user is in
        "user": "U0BOB",
        "text": (
            "<@holmes> can you confirm the root cause and which change "
            "introduced this?"
        ),
        "reply_count": 2,
        "reply_users_count": 2,
        "thread_ts": THREAD_TS,
    },
]

# Replies inside the THREAD_TS thread. Note: still no mention of the release
# version or deploy id — that context only exists earlier in the channel.
_THREAD_REPLIES = [
    {
        "type": "message",
        "ts": "1721003605.000000",
        "user": "U0HOLMES",
        "text": "Acknowledged — investigating the checkout-api 5xx errors.",
        "thread_ts": THREAD_TS,
    },
    {
        "type": "message",
        "ts": "1721003700.000000",
        "user": "U0BOB",
        "text": "thanks — we just need the exact change that caused it.",
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
