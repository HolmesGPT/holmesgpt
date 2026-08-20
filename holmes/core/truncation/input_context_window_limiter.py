"""
Pre-LLM-call context window check — triggers compaction when conversation is too large.

For an overview of all context management mechanisms, see:
docs/reference/context-management.md
"""

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

    This is separated from compact_if_necessary so the caller can yield
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


def needs_to_be_compacted(
    llm: "LLM", messages: list[dict], tools: Optional[list[dict[str, Any]]]
) -> bool:
    """True when the conversation is above the compaction threshold."""
    tokens = llm.count_tokens(messages=messages, tools=tools).total_tokens  # type: ignore
    return (tokens + llm.get_maximum_output_token()) > (
        llm.get_context_window_size() * get_context_window_compaction_threshold_pct() / 100
    )


def compact_before_tool_result_if_needed(
    llm: "LLM",
    messages: list[dict],
    tool_message: dict,
    tools: Optional[list[dict[str, Any]]],
) -> tuple[list[dict], RequestStats]:
    """Compact history before appending a tool result that crosses the threshold."""
    usage = RequestStats()
    if not ENABLE_CONVERSATION_HISTORY_COMPACTION or not needs_to_be_compacted(
        llm, messages + [tool_message], tools
    ):
        return messages, usage
    pending = next(
        (i for i in reversed(range(len(messages))) if messages[i].get("tool_calls")), 0
    )
    if pending > 1:  # compact only what precedes the in-flight tool calls
        result = compact_conversation_history(messages[:pending], llm, tools)
        if result.summary:
            messages = result.messages_after_compaction + messages[pending:]
        if result.usage:
            usage += result.usage
    total = llm.count_tokens(messages=messages + [tool_message], tools=tools).total_tokens  # type: ignore
    if total + llm.get_maximum_output_token() > llm.get_context_window_size():
        logging.warning(f"Tool result too large for the context window ({total} tokens); discarded")
        tool_message["content"] = (
            f"Tool result too large for the context window ({total} tokens total): result discarded. "
            f"Re-run the tool with narrower parameters."
        )
        tool_message.pop("token_count", None)
    return messages, usage


class CompactionInsufficientError(Exception):
    """Raised when conversation compaction was not sufficient to fit the context window."""

    def __init__(self, message: str, events: list[StreamMessage], compaction_usage: Optional[RequestStats] = None):
        """Store the failure message plus the stream events and usage gathered so far."""
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
def compact_if_necessary(
    llm: LLM, messages: list[dict], tools: Optional[list[dict[str, Any]]]
) -> ContextWindowLimiterOutput:
    """Compact the conversation history when it approaches the context-window threshold."""
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
            original_conversation_history=messages, llm=llm, tools=tools
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

            compaction_summary = compaction_result.summary

            compaction_stats: dict = {
                "initial_tokens": initial_tokens.total_tokens,
                "compacted_tokens": compacted_total_tokens,
                "compression_ratio_pct": compression_ratio,
                "num_messages_before": num_messages_before,
                "num_messages_after": num_messages_after,
                "max_context_size": max_context_size,
                "threshold_pct": get_context_window_compaction_threshold_pct(),
                # Whether the cache-friendly primary summarization request was
                # abandoned for the flattened/no-tools fallback (which cannot
                # reuse the agentic prompt cache). Key for diagnosing zero
                # cached_tokens on the compaction call from event data alone.
                "fallback_used": compaction_result.fallback_used,
            }
            if compaction_result.fallback_reason:
                compaction_stats["fallback_reason"] = compaction_result.fallback_reason
            if compaction_usage:
                compaction_stats["compaction_cost"] = {
                    "total_cost": compaction_usage.total_cost,
                    "prompt_tokens": compaction_usage.prompt_tokens,
                    "completion_tokens": compaction_usage.completion_tokens,
                    "total_tokens": compaction_usage.total_tokens,
                    "cached_tokens": compaction_usage.cached_tokens,
                    "cache_creation_tokens": compaction_usage.cache_creation_tokens,
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
    logging.debug(f"compact_if_necessary: {elapsed_ms:.1f}ms total | {tokens.total_tokens} tokens")

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
