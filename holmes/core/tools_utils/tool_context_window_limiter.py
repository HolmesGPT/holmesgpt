import logging
from typing import Optional

from pydantic import BaseModel

from holmes.core.llm import LLM
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResultStatus
from holmes.core.tools_utils.filesystem_result_storage import (
    format_filesystem_pointer_message,
    save_large_result,
)
from holmes.utils import sentry_helper


class ToolCallSizeMetadata(BaseModel):
    messages_token: int
    max_tokens_allowed: int


def get_pct_token_count(percent_of_total_context_window: float, llm: LLM) -> int:
    context_window_size = llm.get_context_window_size()

    if 0 < percent_of_total_context_window and percent_of_total_context_window <= 100:
        return int(context_window_size * percent_of_total_context_window // 100)
    else:
        return context_window_size


def prevent_overly_big_tool_response(
    tool_call_result: ToolCallResult,
    llm: LLM,
    session_id: Optional[str] = None,
) -> int:
    """
    Handle tool results that exceed the context window limit.

    If session_id is provided and filesystem storage is enabled, saves large
    results to filesystem and returns a pointer message to the LLM. Otherwise,
    falls back to dropping the data with an error message.

    Args:
        tool_call_result: The tool call result to check/process
        llm: The LLM instance for token counting
        session_id: Optional session ID for filesystem storage

    Returns:
        The token count of the original message
    """
    message = tool_call_result.as_tool_call_message()
    messages_token = llm.count_tokens(messages=[message]).total_tokens
    max_tokens_allowed = llm.get_max_token_count_for_single_tool()

    if (
        tool_call_result.result.status == StructuredToolResultStatus.SUCCESS
        and messages_token > max_tokens_allowed
    ):
        original_data = tool_call_result.result.data

        # Try filesystem storage if session_id is provided
        file_path = None
        if session_id:
            file_path = save_large_result(
                session_id=session_id,
                tool_call_id=tool_call_result.tool_call_id,
                tool_name=tool_call_result.tool_name,
                data=original_data,
                params=tool_call_result.result.params,
                token_count=messages_token,
            )

        if file_path:
            # Filesystem storage succeeded - return pointer message
            pointer_message = format_filesystem_pointer_message(
                file_path=file_path,
                token_count=messages_token,
                data=original_data,
            )
            tool_call_result.result.status = StructuredToolResultStatus.SUCCESS
            tool_call_result.result.data = pointer_message
            tool_call_result.result.error = None
            logging.info(
                f"Large tool result ({messages_token} tokens) saved to {file_path}"
            )
        else:
            # Filesystem storage disabled or failed - fall back to error message
            relative_pct = (
                (messages_token - max_tokens_allowed) / messages_token
            ) * 100
            error_message = (
                f"The tool call result is too large to return: {messages_token} tokens.\n"
                f"The maximum allowed tokens is {max_tokens_allowed} which is {format(relative_pct, '.1f')}% smaller.\n"
                f"Instructions for the LLM: try to repeat the query but proactively narrow down the result "
                f"so that the tool answer fits within the allowed number of tokens."
            )
            tool_call_result.result.status = StructuredToolResultStatus.ERROR
            tool_call_result.result.data = None
            tool_call_result.result.error = error_message

        sentry_helper.capture_toolcall_contains_too_many_tokens(
            tool_call_result, messages_token, max_tokens_allowed
        )

    return messages_token
