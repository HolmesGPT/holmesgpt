import json
import logging
from enum import Enum
from functools import partial
from typing import Generator, List, Optional, Union

import litellm
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import ModelResponse, TextCompletionResponse
from pydantic import BaseModel, Field

from holmes.core.investigation_structured_output import process_response_into_sections
from holmes.core.llm import TokenCountMetadata, get_llm_usage
from holmes.utils import sentry_helper


class StreamEvents(str, Enum):
    ANSWER_END = "ai_answer_end"
    START_TOOL = "start_tool_calling"
    TOOL_RESULT = "tool_calling_result"
    ERROR = "error"
    AI_MESSAGE = "ai_message"
    APPROVAL_REQUIRED = "approval_required"
    TOKEN_COUNT = "token_count"
    CONVERSATION_HISTORY_COMPACTED = "conversation_history_compacted"


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


def stream_investigate_formatter(
    call_stream: Generator[StreamMessage, None, None], runbooks
):
    try:
        for message in call_stream:
            if message.event == StreamEvents.ANSWER_END:
                (text_response, sections) = process_response_into_sections(  # type: ignore
                    message.data.get("content")
                )

                if sections is None:
                    sentry_helper.capture_sections_none(
                        content=message.data.get("content"),
                    )

                yield create_sse_message(
                    StreamEvents.ANSWER_END.value,
                    {
                        "sections": sections or {},
                        "analysis": text_response,
                        "instructions": runbooks or [],
                        "metadata": message.data.get("metadata") or {},
                    },
                )
            else:
                yield create_sse_message(message.event.value, message.data)
    except litellm.exceptions.RateLimitError as e:
        yield create_rate_limit_error_message(str(e))


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
    except litellm.exceptions.RateLimitError as e:
        yield create_rate_limit_error_message(str(e))
    except Exception as e:
        logging.error(e)
        if "Model is getting throttled" in str(e):  # happens for bedrock
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
