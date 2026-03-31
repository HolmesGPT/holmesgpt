import logging
from typing import Optional

from holmes.common.env_vars import TOOL_CALL_SAFEGUARDS_ENABLED
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


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
            # It is only reasonable to prevent identical tool calls if Holmes is read only
            # and does not mutate resources. If Holmes mutates resources then this safeguard
            # should be removed or modified.
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "Refusing to run this tool call because it has already been called during this session with the exact same parameters.\n"
                    "Move on with your investigation to a different tool or change the parameter values."
                ),
                params=tool_params,
            )
    except Exception:
        logging.error("Failed to check for overly repeated tool call", exc_info=True)

    return None
