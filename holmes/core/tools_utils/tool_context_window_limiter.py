import logging
from typing import Optional

from pydantic import BaseModel

from holmes.common.env_vars import load_bool
from holmes.core.llm import LLM
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResultStatus
from holmes.core.tools_utils.filesystem_result_storage import save_large_result
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
    chat_id: Optional[str] = None,
) -> int:
    """
    Handle tool results that exceed the context window limit.

    If chat_id is provided and filesystem storage is enabled, saves large
    results to filesystem and returns a pointer message to the LLM. Otherwise,
    falls back to dropping the data with an error message.

    Args:
        tool_call_result: The tool call result to check/process
        llm: The LLM instance for token counting
        chat_id: Optional chat ID for filesystem storage

    Returns:
        The token count of the original message
    """
    message = tool_call_result.as_tool_call_message()
    messages_token = llm.count_tokens(messages=[message]).total_tokens
    max_tokens_allowed = llm.get_max_token_count_for_single_tool()

    if tool_call_result.result.status != StructuredToolResultStatus.SUCCESS:
        return messages_token
    if messages_token <= max_tokens_allowed:
        return messages_token

    size_info = (
        f"The tool call result is too large to return: {messages_token}/{max_tokens_allowed} tokens.\n"
    )

    # Try filesystem storage if chat_id is provided and storage is enabled
    file_path = None
    if chat_id and load_bool("HOLMES_TOOL_RESULT_STORAGE_ENABLED", True):
        filesystem_data, is_json = tool_call_result.result.stringify_data(compact=False)
        file_path = save_large_result(
            chat_id=chat_id,
            tool_name=tool_call_result.tool_name,
            tool_call_id=tool_call_result.tool_call_id,
            content=filesystem_data,
            is_json=is_json,
        )

    if file_path:
        # Include a preview (~10% of max tool size) so the LLM sees the same format as the file
        preview_chars = max_tokens_allowed * 4 // 10  # ~4 chars per token, 10% of max
        preview = filesystem_data[:preview_chars]
        tool_call_result.result.data = (
            f"{size_info}\n"
            f"Saved to: {file_path}\n"
            f"Use the bash commands to access the data that won't require prompting the user for approval (e.g. cat, grep, head, tail, jq).\n"
            f"\nPreview:\n{preview}"
        )
        logging.info(
            f"Large tool result ({messages_token} tokens) saved to {file_path}"
        )
    else:
        tool_call_result.result.status = StructuredToolResultStatus.ERROR
        tool_call_result.result.data = None
        tool_call_result.result.error = (
            f"{size_info}\n"
            f"Try to repeat the query but proactively narrow down the result "
            f"so that the tool answer fits within the allowed number of tokens."
        )

    sentry_helper.capture_toolcall_contains_too_many_tokens(
        tool_call_result, messages_token, max_tokens_allowed
    )
    return messages_token
