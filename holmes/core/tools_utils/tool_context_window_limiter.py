from typing import Optional
from pydantic import BaseModel
from holmes.core.llm import LLM
from holmes.core.tools import StructuredToolResultStatus
from holmes.core.models import ToolCallResult
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


def is_tool_call_too_big(
    tool_call_result: ToolCallResult, llm: LLM
) -> tuple[bool, Optional[ToolCallSizeMetadata]]:
    if tool_call_result.result.status == StructuredToolResultStatus.SUCCESS:
        message = tool_call_result.as_tool_call_message()

        tokens = llm.count_tokens(messages=[message])
        max_tokens_allowed = llm.get_max_token_count_for_single_tool()
        return (
            tokens.total_tokens > max_tokens_allowed,
            ToolCallSizeMetadata(
                messages_token=tokens.total_tokens,
                max_tokens_allowed=max_tokens_allowed,
            ),
        )
    return False, None


def prevent_overly_big_tool_response(tool_call_result: ToolCallResult, llm: LLM):
    tool_call_result_is_too_big, metadata = is_tool_call_too_big(
        tool_call_result=tool_call_result, llm=llm
    )
    if tool_call_result_is_too_big and metadata:
        relative_pct = (
            (metadata.messages_token - metadata.max_tokens_allowed)
            / metadata.messages_token
        ) * 100
        error_message = f"The tool call result is too large to return: {metadata.messages_token} tokens.\nThe maximum allowed tokens is {metadata.max_tokens_allowed} which is {format(relative_pct, '.1f')}% smaller.\nInstructions for the LLM: try to repeat the query but proactively narrow down the result so that the tool answer fits within the allowed number of tokens."
        tool_call_result.result.status = StructuredToolResultStatus.ERROR
        tool_call_result.result.data = None
        tool_call_result.result.error = error_message

        sentry_helper.capture_toolcall_contains_too_many_tokens(
            tool_call_result, metadata.messages_token, metadata.max_tokens_allowed
        )
