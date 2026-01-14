import json
import logging
import time
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


class TimingEvent(BaseModel):
    """Represents a single timing event during request processing."""
    event_type: str  # e.g., "llm_call_start", "llm_call_end", "tool_call_start", etc.
    timestamp: float  # Time since request start in seconds
    description: Optional[str] = None  # Additional context about the event
    metadata: Optional[dict] = None  # Extra metadata (e.g., model name, tool name)


class StreamMessage(BaseModel):
    event: StreamEvents
    data: dict = Field(default={})
    timing: Optional[dict] = None  # Contains elapsed_time_ms and timing_events


class TimingTracker:
    """Tracks timing events throughout the streaming request lifecycle."""

    def __init__(self):
        self.start_time = time.time()
        self.events: List[TimingEvent] = []
        self._add_event("request_start", "Request processing started")

    def _add_event(self, event_type: str, description: Optional[str] = None, metadata: Optional[dict] = None):
        """Add a timing event."""
        elapsed = time.time() - self.start_time
        self.events.append(TimingEvent(
            event_type=event_type,
            timestamp=elapsed,
            description=description,
            metadata=metadata
        ))

    def record_llm_call_start(self, model: Optional[str] = None, iteration: Optional[int] = None):
        """Record the start of an LLM completion call."""
        metadata: dict = {}
        if model:
            metadata["model"] = model
        if iteration is not None:
            metadata["iteration"] = str(iteration)
        self._add_event("llm_call_start", f"LLM completion call started (iteration {iteration})", metadata)

    def record_llm_call_end(self, model: Optional[str] = None, iteration: Optional[int] = None):
        """Record the end of an LLM completion call."""
        metadata: dict = {}
        if model:
            metadata["model"] = model
        if iteration is not None:
            metadata["iteration"] = str(iteration)
        self._add_event("llm_call_end", f"LLM completion call completed (iteration {iteration})", metadata)

    def record_tool_call_start(self, tool_name: str, tool_id: str):
        """Record the start of a tool call."""
        self._add_event("tool_call_start", f"Tool call started: {tool_name}", {"tool_name": tool_name, "tool_id": tool_id})

    def record_tool_call_end(self, tool_name: str, tool_id: str):
        """Record the end of a tool call."""
        self._add_event("tool_call_end", f"Tool call completed: {tool_name}", {"tool_name": tool_name, "tool_id": tool_id})

    def record_history_compaction(self):
        """Record when conversation history is compacted."""
        self._add_event("history_compacted", "Conversation history was compacted")

    def record_config_load_start(self):
        """Record when config/toolset loading starts."""
        self._add_event("config_load_start", "Loading configuration and toolsets")

    def record_config_load_end(self, num_toolsets: Optional[int] = None):
        """Record when config/toolset loading completes."""
        metadata = {"num_toolsets": str(num_toolsets)} if num_toolsets else None
        self._add_event("config_load_end", f"Configuration loaded with {num_toolsets} toolsets", metadata)

    def record_message_build_start(self):
        """Record when message building starts."""
        self._add_event("message_build_start", "Building messages for LLM")

    def record_message_build_end(self, num_messages: Optional[int] = None):
        """Record when message building completes."""
        metadata = {"num_messages": str(num_messages)} if num_messages else None
        self._add_event("message_build_end", f"Built {num_messages} messages", metadata)

    def record_dal_operation(self, operation: str, duration_ms: Optional[float] = None):
        """Record a database/DAL operation."""
        metadata = {"operation": operation}
        if duration_ms is not None:
            metadata["duration_ms"] = str(round(duration_ms, 2))
        self._add_event("dal_operation", f"Database operation: {operation}", metadata)

    def record_context_window_limiting_start(self):
        """Record when context window limiting starts."""
        self._add_event("context_limiting_start", "Starting context window limiting")

    def record_context_window_limiting_end(self, tokens_before: Optional[int] = None, tokens_after: Optional[int] = None):
        """Record when context window limiting completes."""
        metadata = {}
        if tokens_before is not None:
            metadata["tokens_before"] = str(tokens_before)
        if tokens_after is not None:
            metadata["tokens_after"] = str(tokens_after)
        self._add_event("context_limiting_end", "Context window limiting completed", metadata)

    def record_prompt_construction_start(self):
        """Record when prompt construction starts."""
        self._add_event("prompt_construction_start", "Constructing prompts with runbooks")

    def record_prompt_construction_end(self, num_runbooks: Optional[int] = None):
        """Record when prompt construction completes."""
        metadata = {"num_runbooks": str(num_runbooks)} if num_runbooks else None
        self._add_event("prompt_construction_end", f"Prompts constructed with {num_runbooks} runbooks", metadata)

    def get_timing_info(self) -> dict:
        """Get current timing information as a dictionary."""
        elapsed_ms = (time.time() - self.start_time) * 1000
        return {
            "elapsed_time_ms": round(elapsed_ms, 2),
            "timing_events": [event.model_dump() for event in self.events]
        }


def create_sse_message(event_type: str, data: Optional[dict] = None, timing_info: Optional[dict] = None):
    if data is None:
        data = {}
    # Add timing information to the data payload
    if timing_info:
        data["timing"] = timing_info
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
    call_stream: Generator[StreamMessage, None, None],
    runbooks,
    timing_tracker: Optional[TimingTracker] = None,
):
    try:
        for message in call_stream:
            # Get current timing info if tracker is available
            timing_info = timing_tracker.get_timing_info() if timing_tracker else None

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
                    timing_info,
                )
            else:
                yield create_sse_message(message.event.value, message.data, timing_info)
    except litellm.exceptions.RateLimitError as e:
        yield create_rate_limit_error_message(str(e))


def stream_chat_formatter(
    call_stream: Generator[StreamMessage, None, None],
    followups: Optional[List[dict]] = None,
    timing_tracker: Optional[TimingTracker] = None,
):
    try:
        for message in call_stream:
            # Get current timing info if tracker is available
            timing_info = timing_tracker.get_timing_info() if timing_tracker else None

            if message.event == StreamEvents.ANSWER_END:
                response_data = {
                    "analysis": message.data.get("content"),
                    "conversation_history": message.data.get("messages"),
                    "follow_up_actions": followups,
                    "metadata": message.data.get("metadata") or {},
                }

                yield create_sse_message(StreamEvents.ANSWER_END.value, response_data, timing_info)
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
                    StreamEvents.APPROVAL_REQUIRED.value, response_data, timing_info
                )
            else:
                yield create_sse_message(message.event.value, message.data, timing_info)
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
