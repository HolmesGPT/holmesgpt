import logging
from typing import Optional

from holmes.common.env_vars import TOOL_CALL_SAFEGUARDS_ENABLED
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

# Tools that only record agent state and fetch no data. Repeating an identical
# call to them is a no-op, so it succeeds instead of being rejected: a rejection
# tells the model something went wrong, and models typically answer that by
# sending the same call again, which loops until max_steps.
IDEMPOTENT_STATE_TOOLS = frozenset({"TodoWrite"})

# Present in both guard responses, so the agent loop can recognise them.
DUPLICATE_CALL_MARKER = "[duplicate tool call]"

# After this many consecutive guarded calls, the agent loop stops offering tools
# and asks for the final answer (see should_force_final_answer).
MAX_CONSECUTIVE_DUPLICATE_CALLS = 3


def _has_previous_exact_same_tool_call(
    tool_name: str, tool_params: dict, tool_calls: list[dict]
) -> bool:
    """Check if a previous tool call with the exact same params was executed this session."""
    for tool_call in tool_calls:
        params = tool_call.get("result", {}).get("params")
        if (
            tool_call.get("tool_name") == tool_name
            and params is not None
            and params == tool_params
        ):
            return True

    return False


def prevent_overly_repeated_tool_call(
    tool_name: str, tool_params: dict, tool_calls: list[dict]
) -> Optional[StructuredToolResult]:
    """Checks if a tool call is redundant"""

    try:
        if not TOOL_CALL_SAFEGUARDS_ENABLED:
            return None

        if _has_previous_exact_same_tool_call(
            tool_name=tool_name, tool_params=tool_params, tool_calls=tool_calls
        ):
            if tool_name in IDEMPOTENT_STATE_TOOLS:
                # The requested state is already in place: report success.
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=(
                        f"{DUPLICATE_CALL_MARKER} No change: this is identical to your "
                        f"previous {tool_name} call, which was already saved. Do not call "
                        f"{tool_name} again with the same content. If your investigation "
                        "is complete, write your final answer now without calling more tools."
                    ),
                    params=tool_params,
                )

            # It is only reasonable to prevent identical tool calls if Holmes is read only
            # and does not mutate resources. If Holmes mutates resources then this safeguard
            # should be removed or modified.
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"{DUPLICATE_CALL_MARKER} Refusing to run this tool call because it has "
                    "already been called during this session with the exact same parameters. "
                    "Its result is already in the conversation above.\n"
                    "If you have enough information, stop calling tools and write your final "
                    "answer now. Otherwise, use a different tool or change the parameter values."
                ),
                params=tool_params,
            )
    except Exception:
        logging.error("Failed to check for overly repeated tool call", exc_info=True)

    return None


def is_duplicate_guard_result(tool_call: dict) -> bool:
    """True if this tool call result was produced by prevent_overly_repeated_tool_call."""
    result = tool_call.get("result") or {}
    text = f"{result.get('error') or ''} {result.get('data') or ''}"
    return DUPLICATE_CALL_MARKER in text


def should_force_final_answer(
    tool_calls: list[dict], limit: int = MAX_CONSECUTIVE_DUPLICATE_CALLS
) -> bool:
    """True if the last `limit` tool calls were all repeats caught by the guard.

    At that point the model is looping: it keeps re-sending calls it already made
    instead of answering. The agent loop then withholds tools for the next LLM call,
    so the model has to write its final answer.
    """
    if not TOOL_CALL_SAFEGUARDS_ENABLED or limit <= 0 or len(tool_calls) < limit:
        return False
    return all(is_duplicate_guard_result(tc) for tc in tool_calls[-limit:])
