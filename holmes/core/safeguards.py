import logging
from typing import Optional

from pydantic import ValidationError

from holmes.common.env_vars import TOOL_CALL_SAFEGUARDS_ENABLED
from holmes.plugins.toolsets.logging_utils.logging_api import POD_LOGGING_TOOL_NAME
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams


def _is_redundant_fetch_pod_logs(
    tool_name: str, tool_params: dict, tool_calls: list[dict]
) -> bool:
    """
    Tool call is redundant if a previous call without filter returned no results and the current tool call is the same but with a filter
    e.g.
        fetch_pod_logs({"pod_name": "notification-consumer", "namespace": "services"}) => no data
    followed by
        fetch_pod_logs({"pod_name": "notification-consumer", "namespace": "services", "filter": "error"}) => for sure no data either
    """
    if (
        tool_name == POD_LOGGING_TOOL_NAME
        and tool_params.get("filter")
        and _has_previous_unfiltered_pod_logs_call(
            tool_params=tool_params, tool_calls=tool_calls
        )
    ):
        return True
    return False


def _has_previous_unfiltered_pod_logs_call(
    tool_params: dict, tool_calls: list[dict]
) -> bool:
    try:
        current_params = FetchPodLogsParams(**tool_params)
        for tool_call in tool_calls:
            result = tool_call.get("result", {})
            if (
                tool_call.get("tool_name") == POD_LOGGING_TOOL_NAME
                and result.get("status") == StructuredToolResultStatus.NO_DATA
                and result.get("params")
            ):
                params = FetchPodLogsParams(**result.get("params"))
                if (
                    not params.filter
                    and current_params.end_time == params.end_time
                    and current_params.start_time == params.start_time
                    and current_params.pod_name == params.pod_name
                    and current_params.namespace == params.namespace
                ):
                    return True

        return False

    except ValidationError:
        logging.error("fetch_pod_logs params failed validation", exc_info=True)
    return False


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
            """
                It is only reasonable to prevent identical tool calls if Holmes is read only and does not mutate resources.
                If Holmes mutate resources then this safeguard should be removed or modified. This is because
                there is a risk that one of the tools holmes executed would actually change the answer of a subsequent identical tool call.
                For example if Holmes checks if a resource is deployed, runs a command to deploy it and then checks again if it has deployed properly.
            """
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "Refusing to run this tool call because it has already been called during this session with the exact same parameters.\n"
                    "Move on with your investigation to a different tool or change the parameter values."
                ),
                params=tool_params,
            )

        if _is_redundant_fetch_pod_logs(
            tool_name=tool_name, tool_params=tool_params, tool_calls=tool_calls
        ):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Refusing to run this tool call because the exact same {POD_LOGGING_TOOL_NAME} tool call without filter has already run and returned no data.\n"
                    "This tool call would also have returned no data.\n"
                    "Move on with your investigation to a different tool or extend the time window of your search."
                ),
                params=tool_params,
            )
    except Exception:
        logging.error("Failed to check for overly repeated tool call", exc_info=True)

    return None
