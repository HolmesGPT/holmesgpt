import logging
import time
from typing import Any, Optional

import sentry_sdk
from pydantic import BaseModel

from holmes.common.env_vars import ENABLE_CONVERSATION_HISTORY_COMPACTION
from holmes.core.llm import (
    LLM,
    ContextWindowUsage,
    get_context_window_compaction_threshold_pct,
)
from holmes.core.llm_usage import RequestStats
from holmes.core.truncation.compaction import compact_conversation_history
from holmes.utils.stream import StreamEvents, StreamMessage


def check_compaction_needed(
    llm: "LLM", messages: list[dict], tools: Optional[list[dict[str, Any]]]
) -> Optional[StreamMessage]:
    """Check if compaction is needed and return a COMPACTION_START event if so.

    This is separated from limit_input_context_window so the caller can yield
    the START event to the SSE stream *before* the blocking compaction call.
    """
    if not ENABLE_CONVERSATION_HISTORY_COMPACTION:
        return None

    initial_tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    max_context_size = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()

    if (initial_tokens.total_tokens + maximum_output_token) > (
        max_context_size * get_context_window_compaction_threshold_pct() / 100
    ):
        num_messages = len(messages)
        return StreamMessage(
            event=StreamEvents.CONVERSATION_HISTORY_COMPACTION_START,
            data={
                "content": f"Compacting conversation history ({initial_tokens.total_tokens} tokens, {num_messages} messages)...",
                "metadata": {
                    "initial_tokens": initial_tokens.total_tokens,
                    "num_messages": num_messages,
                    "max_context_size": max_context_size,
                    "threshold_pct": get_context_window_compaction_threshold_pct(),
                },
            },
        )
    return None


class CompactionInsufficientError(Exception):
    """Raised when conversation compaction was not sufficient to fit the context window."""

    def __init__(self, message: str, events: list[StreamMessage], compaction_usage: Optional[RequestStats] = None):
        super().__init__(message)
        self.events = events
        self.compaction_usage = compaction_usage


class ContextWindowLimiterOutput(BaseModel):
    metadata: dict
    messages: list[dict]
    events: list[StreamMessage]
    max_context_size: int
    maximum_output_token: int
    tokens: ContextWindowUsage
    conversation_history_compacted: bool
    compaction_usage: Optional["RequestStats"] = None


@sentry_sdk.trace
def limit_input_context_window(
    llm: LLM, messages: list[dict], tools: Optional[list[dict[str, Any]]]
) -> ContextWindowLimiterOutput:
    t0 = time.monotonic()
    events = []
    metadata = {}
    initial_tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    max_context_size = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()
    conversation_history_compacted = False
    compaction_usage = RequestStats()
    if ENABLE_CONVERSATION_HISTORY_COMPACTION and (
        initial_tokens.total_tokens + maximum_output_token
    ) > (max_context_size * get_context_window_compaction_threshold_pct() / 100):
        num_messages_before = len(messages)
        compaction_result = compact_conversation_history(
            original_conversation_history=messages, llm=llm
        )
        compaction_usage = compaction_result.usage
        compacted_tokens = llm.count_tokens(compaction_result.messages_after_compaction, tools=tools)
        compacted_total_tokens = compacted_tokens.total_tokens

        if compacted_total_tokens < initial_tokens.total_tokens:
            messages = compaction_result.messages_after_compaction
            num_messages_after = len(messages)
            compression_ratio = round((1 - compacted_total_tokens / initial_tokens.total_tokens) * 100, 1)
            compaction_message = f"The conversation history has been compacted from {initial_tokens.total_tokens} to {compacted_total_tokens} tokens"
            logging.info(compaction_message)
            conversation_history_compacted = True

            # Extract the LLM-generated summary from the compacted messages
            # Structure is: [system_prompt?, last_user_prompt?, assistant_summary, continuation_marker]
            compaction_summary = None
            for msg in compaction_result.messages_after_compaction:
                if msg.get("role") == "assistant":
                    compaction_summary = msg.get("content")
                    break

            compaction_stats: dict = {
                "initial_tokens": initial_tokens.total_tokens,
                "compacted_tokens": compacted_total_tokens,
                "compression_ratio_pct": compression_ratio,
                "num_messages_before": num_messages_before,
                "num_messages_after": num_messages_after,
                "max_context_size": max_context_size,
                "threshold_pct": get_context_window_compaction_threshold_pct(),
            }
            if compaction_usage:
                compaction_stats["compaction_cost"] = {
                    "total_cost": compaction_usage.total_cost,
                    "prompt_tokens": compaction_usage.prompt_tokens,
                    "completion_tokens": compaction_usage.completion_tokens,
                    "total_tokens": compaction_usage.total_tokens,
                }

            events.append(
                StreamMessage(
                    event=StreamEvents.CONVERSATION_HISTORY_COMPACTED,
                    data={
                        "content": compaction_message,
                        "compaction_summary": compaction_summary,
                        "messages": compaction_result.messages_after_compaction,
                        "metadata": compaction_stats,
                    },
                )
            )
            events.append(
                StreamMessage(
                    event=StreamEvents.AI_MESSAGE,
                    data={"content": compaction_message},
                )
            )
        else:
            logging.error(
                f"Failed to reduce token count when compacting conversation history. Original tokens:{initial_tokens.total_tokens}. Compacted tokens:{compacted_total_tokens}"
            )

    tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    if (tokens.total_tokens + maximum_output_token) > max_context_size:
        if ENABLE_CONVERSATION_HISTORY_COMPACTION:
            failure_msg = (
                f"Conversation history compaction failed to reduce tokens sufficiently. "
                f"Current: {tokens.total_tokens} tokens + {maximum_output_token} max output = "
                f"{tokens.total_tokens + maximum_output_token}, but context window is {max_context_size}. "
                f"Please start a new conversation."
            )
        else:
            failure_msg = (
                f"Conversation history exceeds the context window and compaction is disabled. "
                f"Current: {tokens.total_tokens} tokens + {maximum_output_token} max output = "
                f"{tokens.total_tokens + maximum_output_token}, but context window is {max_context_size}. "
                f"Please start a new conversation."
            )
        logging.error(failure_msg)
        events.append(
            StreamMessage(
                event=StreamEvents.AI_MESSAGE,
                data={"content": failure_msg},
            )
        )
        raise CompactionInsufficientError(failure_msg, events=events, compaction_usage=compaction_usage)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logging.debug(f"limit_input_context_window: {elapsed_ms:.1f}ms total | {tokens.total_tokens} tokens")

    return ContextWindowLimiterOutput(
        events=events,
        messages=messages,
        metadata=metadata,
        max_context_size=max_context_size,
        maximum_output_token=maximum_output_token,
        tokens=tokens,
        conversation_history_compacted=conversation_history_compacted,
        compaction_usage=compaction_usage,
    )
