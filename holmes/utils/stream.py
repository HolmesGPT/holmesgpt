import json
import logging
from enum import Enum
from functools import partial
from typing import Generator, List, Optional, Union

import litellm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse, TextCompletionResponse
from pydantic import BaseModel, Field

from holmes.core.llm import TokenCountMetadata, get_llm_usage


class StreamEvents(str, Enum):
    ANSWER_END = "ai_answer_end"
    START_TOOL = "start_tool_calling"
    TOOL_RESULT = "tool_calling_result"
    ERROR = "error"
    AI_MESSAGE = "ai_message"
    APPROVAL_REQUIRED = "approval_required"
    TOKEN_COUNT = "token_count"
    CONVERSATION_HISTORY_COMPACTED = "conversation_history_compacted"
    # LLM iteration events for OTEL tracing
    LLM_ITERATION_START = "llm_iteration_start"
    LLM_ITERATION_COMPLETE = "llm_iteration_complete"
    # Granular span events for detailed tracing
    TOOL_INVOKE_START = "tool_invoke_start"
    TOOL_INVOKE_END = "tool_invoke_end"
    PARSE_RESPONSE_START = "parse_response_start"
    PARSE_RESPONSE_END = "parse_response_end"
    CONTEXT_CHECK_START = "context_check_start"
    CONTEXT_CHECK_END = "context_check_end"
    ERROR_HANDLING_START = "error_handling_start"
    ERROR_HANDLING_END = "error_handling_end"


class StreamMessage(BaseModel):
    event: StreamEvents
    data: dict = Field(default={})


def create_sse_message(event_type: str, data: Optional[dict] = None):
    if data is None:
        data = {}
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def create_sse_error_message(description: str, error_code: int, msg: str):
    return create_sse_message(
        StreamEvents.ERROR.value,
        {
            "description": description,
            "error_code": error_code,
            "msg": msg,
            "success": False,
        },
    )


create_rate_limit_error_message = partial(
    create_sse_error_message,
    error_code=5204,
    msg="Rate limit exceeded",
)


def _is_rate_limit_error(e: Exception) -> bool:
    """Check if an exception is a rate limit error.

    Bedrock raises a generic Exception with 'Model is getting throttled'
    instead of litellm.exceptions.RateLimitError, so we need a string check
    as a fallback.
    """
    return isinstance(e, litellm.exceptions.RateLimitError) or "Model is getting throttled" in str(e)


def stream_chat_formatter(
    call_stream: Generator[StreamMessage, None, None],
    followups: Optional[List[dict]] = None,
):
    try:
        for message in call_stream:
            if message.event == StreamEvents.ANSWER_END:
                response_data = {
                    "analysis": message.data.get("content"),
                    "conversation_history": message.data.get("messages"),
                    "follow_up_actions": followups,
                    "metadata": message.data.get("metadata") or {},
                }

                yield create_sse_message(StreamEvents.ANSWER_END.value, response_data)
            elif message.event == StreamEvents.APPROVAL_REQUIRED:
                response_data = {
                    "analysis": message.data.get("content"),
                    "conversation_history": message.data.get("messages"),
                    "follow_up_actions": followups,
                }

                response_data["requires_approval"] = True
                response_data["pending_approvals"] = message.data.get(
                    "pending_approvals", []
                )

                yield create_sse_message(
                    StreamEvents.APPROVAL_REQUIRED.value, response_data
                )
            else:
                yield create_sse_message(message.event.value, message.data)
    except Exception as e:
        logging.error(f"Error during streaming chat: {e}", exc_info=True)
        if _is_rate_limit_error(e):
            yield create_rate_limit_error_message(str(e))
        else:
            yield create_sse_error_message(description=str(e), error_code=1, msg=str(e))


def add_token_count_to_metadata(
    tokens: TokenCountMetadata,
    metadata: dict,
    max_context_size: int,
    maximum_output_token: int,
    full_llm_response: Union[
        ModelResponse, CustomStreamWrapper, TextCompletionResponse
    ],
):
    metadata["usage"] = get_llm_usage(full_llm_response)
    metadata["tokens"] = tokens.model_dump()
    metadata["max_tokens"] = max_context_size
    metadata["max_output_tokens"] = maximum_output_token


def build_stream_event_token_count(metadata: dict) -> StreamMessage:
    return StreamMessage(
        event=StreamEvents.TOKEN_COUNT,
        data={
            "metadata": metadata,
        },
    )


def build_stream_event_llm_iteration_start(
    iteration: int,
    model: str,
) -> StreamMessage:
    """Build a stream event for the start of an LLM iteration.

    Args:
        iteration: The iteration number (1-indexed)
        model: The model being used for this iteration
    """
    return StreamMessage(
        event=StreamEvents.LLM_ITERATION_START,
        data={
            "iteration": iteration,
            "model": model,
        },
    )


def build_stream_event_llm_iteration_complete(
    iteration: int,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    finish_reason: Optional[str] = None,
    cost_usd: Optional[float] = None,
) -> StreamMessage:
    """Build a stream event for completion of an LLM iteration.

    Args:
        iteration: The iteration number (1-indexed)
        model: The model used for this iteration
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        total_tokens: Total tokens used
        finish_reason: Why the LLM stopped (e.g., 'stop', 'tool_calls')
        cost_usd: Cost in USD for this iteration (if available)
    """
    data = {
        "iteration": iteration,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if finish_reason is not None:
        data["finish_reason"] = finish_reason
    if cost_usd is not None:
        data["cost_usd"] = cost_usd
    return StreamMessage(
        event=StreamEvents.LLM_ITERATION_COMPLETE,
        data=data,
    )


def build_stream_event_tool_invoke_start(
    tool_name: str,
    tool_call_id: str,
    tool_arguments: Optional[str] = None,
) -> StreamMessage:
    """Build a stream event for the start of tool invocation.

    Args:
        tool_name: Name of the tool being invoked
        tool_call_id: Unique identifier for this tool call
        tool_arguments: JSON string of tool arguments (will be truncated)
    """
    data = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
    }
    if tool_arguments is not None:
        # Truncate arguments to prevent large payloads
        data["tool_arguments"] = tool_arguments[:8192] if len(tool_arguments) > 8192 else tool_arguments
    return StreamMessage(
        event=StreamEvents.TOOL_INVOKE_START,
        data=data,
    )


def build_stream_event_tool_invoke_end(
    tool_name: str,
    tool_call_id: str,
    duration_ms: int,
    status: str,
    result: Optional[str] = None,
    error: Optional[str] = None,
) -> StreamMessage:
    """Build a stream event for the end of tool invocation.

    Args:
        tool_name: Name of the tool that was invoked
        tool_call_id: Unique identifier for this tool call
        duration_ms: How long the tool took in milliseconds
        status: SUCCESS or FAILURE
        result: Tool result (will be truncated)
        error: Error message if status is FAILURE
    """
    data = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "duration_ms": duration_ms,
        "status": status,
    }
    if result is not None:
        data["result"] = result[:8192] if len(result) > 8192 else result
    if error is not None:
        data["error"] = error
    return StreamMessage(
        event=StreamEvents.TOOL_INVOKE_END,
        data=data,
    )


def build_stream_event_parse_response(
    is_start: bool,
    tool_call_count: Optional[int] = None,
    finish_reason: Optional[str] = None,
) -> StreamMessage:
    """Build a stream event for response parsing start/end.

    Args:
        is_start: True for start event, False for end event
        tool_call_count: Number of tool calls found (only for end event)
        finish_reason: LLM finish reason (only for end event)
    """
    event = StreamEvents.PARSE_RESPONSE_START if is_start else StreamEvents.PARSE_RESPONSE_END
    data = {}
    if not is_start:
        if tool_call_count is not None:
            data["tool_call_count"] = tool_call_count
        if finish_reason is not None:
            data["finish_reason"] = finish_reason
    return StreamMessage(event=event, data=data)


def build_stream_event_context_check(
    is_start: bool,
    tokens_used: Optional[int] = None,
    tokens_limit: Optional[int] = None,
    compaction_needed: Optional[bool] = None,
) -> StreamMessage:
    """Build a stream event for context limit checking.

    Args:
        is_start: True for start event, False for end event
        tokens_used: Current token count (only for end event)
        tokens_limit: Token limit (only for end event)
        compaction_needed: Whether history compaction was needed (only for end event)
    """
    event = StreamEvents.CONTEXT_CHECK_START if is_start else StreamEvents.CONTEXT_CHECK_END
    data = {}
    if not is_start:
        if tokens_used is not None:
            data["tokens_used"] = tokens_used
        if tokens_limit is not None:
            data["tokens_limit"] = tokens_limit
        if compaction_needed is not None:
            data["compaction_needed"] = compaction_needed
    return StreamMessage(event=event, data=data)


def build_stream_event_error_handling(
    is_start: bool,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    will_retry: Optional[bool] = None,
) -> StreamMessage:
    """Build a stream event for error handling.

    Args:
        is_start: True for start event, False for end event
        error_type: Type of error encountered
        error_message: Error message
        will_retry: Whether the operation will be retried
    """
    event = StreamEvents.ERROR_HANDLING_START if is_start else StreamEvents.ERROR_HANDLING_END
    data = {}
    if error_type is not None:
        data["error_type"] = error_type
    if error_message is not None:
        data["error_message"] = error_message[:1024] if len(error_message) > 1024 else error_message
    if will_retry is not None:
        data["will_retry"] = will_retry
    return StreamMessage(event=event, data=data)
