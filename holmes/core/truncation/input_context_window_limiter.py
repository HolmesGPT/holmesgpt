import logging
from typing import Any, Optional

import sentry_sdk
from pydantic import BaseModel

from holmes.common.env_vars import (
    ENABLE_CONVERSATION_HISTORY_COMPACTION,
    MAX_OUTPUT_TOKEN_RESERVATION,
)
from holmes.core.llm import (
    LLM,
    TokenCountMetadata,
    get_context_window_compaction_threshold_pct,
)
from holmes.core.models import TruncationMetadata, TruncationResult
from holmes.core.truncation.compaction import compact_conversation_history
from holmes.utils import sentry_helper
from holmes.utils.stream import StreamEvents, StreamMessage

TRUNCATION_NOTICE = "\n\n[TRUNCATED]"


def _truncate_tool_message(
    msg: dict, allocated_space: int, needed_space: int
) -> TruncationMetadata:
    msg_content = msg["content"]
    tool_call_id = msg["tool_call_id"]
    tool_name = msg["name"]

    # Ensure the indicator fits in the allocated space
    if allocated_space > len(TRUNCATION_NOTICE):
        original = msg_content if isinstance(msg_content, str) else str(msg_content)
        msg["content"] = (
            original[: allocated_space - len(TRUNCATION_NOTICE)] + TRUNCATION_NOTICE
        )
        end_index = allocated_space - len(TRUNCATION_NOTICE)
    else:
        msg["content"] = TRUNCATION_NOTICE[:allocated_space]
        end_index = allocated_space

    msg.pop("token_count", None)  # Remove token_count if present
    logging.info(
        f"Truncating tool message '{tool_name}' from {needed_space} to {allocated_space} tokens"
    )
    truncation_metadata = TruncationMetadata(
        tool_call_id=tool_call_id,
        start_index=0,
        end_index=end_index,
        tool_name=tool_name,
        original_token_count=needed_space,
    )
    return truncation_metadata


# TODO: I think there's a bug here because we don't account for the 'role' or json structure like '{...}' when counting tokens
# However, in practice it works because we reserve enough space for the output tokens that the minor inconsistency does not matter
# We should fix this in the future
# TODO: we truncate using character counts not token counts - this means we're overly agressive with truncation - improve it by considering
# token truncation and not character truncation
def truncate_messages_to_fit_context(
    messages: list, max_context_size: int, maximum_output_token: int, count_tokens_fn
) -> TruncationResult:
    """
    Helper function to truncate tool messages to fit within context limits.

    Args:
        messages: List of message dictionaries with roles and content
        max_context_size: Maximum context window size for the model
        maximum_output_token: Maximum tokens reserved for model output
        count_tokens_fn: Function to count tokens for a list of messages

    Returns:
        Modified list of messages with truncated tool responses

    Raises:
        Exception: If non-tool messages exceed available context space
    """
    messages_except_tools = [
        message for message in messages if message["role"] != "tool"
    ]
    tokens = count_tokens_fn(messages_except_tools)
    message_size_without_tools = tokens.total_tokens

    tool_call_messages = [message for message in messages if message["role"] == "tool"]

    reserved_for_output_tokens = min(maximum_output_token, MAX_OUTPUT_TOKEN_RESERVATION)
    if message_size_without_tools >= (max_context_size - reserved_for_output_tokens):
        logging.error(
            f"The combined size of system_prompt and user_prompt ({message_size_without_tools} tokens) exceeds the model's context window for input."
        )
        raise Exception(
            f"The combined size of system_prompt and user_prompt ({message_size_without_tools} tokens) exceeds the maximum context size of {max_context_size - reserved_for_output_tokens} tokens available for input."
        )

    if len(tool_call_messages) == 0:
        return TruncationResult(truncated_messages=messages, truncations=[])

    available_space = (
        max_context_size - message_size_without_tools - reserved_for_output_tokens
    )
    remaining_space = available_space
    tool_call_messages.sort(
        key=lambda x: count_tokens_fn(
            [{"role": "tool", "content": x["content"]}]
        ).total_tokens
    )

    truncations = []

    # Allocate space starting with small tools and going to larger tools, while maintaining fairness
    # Small tools can often get exactly what they need, while larger tools may need to be truncated
    # We ensure fairness (no tool gets more than others that need it) and also maximize utilization (we don't leave space unused)
    for i, msg in enumerate(tool_call_messages):
        remaining_tools = len(tool_call_messages) - i
        max_allocation = remaining_space // remaining_tools
        needed_space = count_tokens_fn(
            [{"role": "tool", "content": msg["content"]}]
        ).total_tokens
        allocated_space = min(needed_space, max_allocation)

        if needed_space > allocated_space:
            truncation_metadata = _truncate_tool_message(
                msg, allocated_space, needed_space
            )
            truncations.append(truncation_metadata)

        remaining_space -= allocated_space

    if truncations:
        sentry_helper.capture_tool_truncations(truncations)

    return TruncationResult(truncated_messages=messages, truncations=truncations)


class ContextWindowLimiterOutput(BaseModel):
    metadata: dict
    messages: list[dict]
    events: list[StreamMessage]
    max_context_size: int
    maximum_output_token: int
    tokens: TokenCountMetadata
    conversation_history_compacted: bool


@sentry_sdk.trace
def limit_input_context_window(
    llm: LLM, messages: list[dict], tools: Optional[list[dict[str, Any]]]
) -> ContextWindowLimiterOutput:
    events = []
    metadata = {}
    initial_tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    max_context_size = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()
    conversation_history_compacted = False
    if ENABLE_CONVERSATION_HISTORY_COMPACTION and (
        initial_tokens.total_tokens + maximum_output_token
    ) > (max_context_size * get_context_window_compaction_threshold_pct() / 100):
        compacted_messages = compact_conversation_history(
            original_conversation_history=messages, llm=llm
        )
        compacted_tokens = llm.count_tokens(compacted_messages, tools=tools)
        compacted_total_tokens = compacted_tokens.total_tokens

        if compacted_total_tokens < initial_tokens.total_tokens:
            messages = compacted_messages
            compaction_message = f"The conversation history has been compacted from {initial_tokens.total_tokens} to {compacted_total_tokens} tokens"
            logging.info(compaction_message)
            conversation_history_compacted = True
            events.append(
                StreamMessage(
                    event=StreamEvents.CONVERSATION_HISTORY_COMPACTED,
                    data={
                        "content": compaction_message,
                        "messages": compacted_messages,
                        "metadata": {
                            "initial_tokens": initial_tokens.total_tokens,
                            "compacted_tokens": compacted_total_tokens,
                        },
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
            logging.debug(
                f"Failed to reduce token count when compacting conversation history. Original tokens:{initial_tokens.total_tokens}. Compacted tokens:{compacted_total_tokens}"
            )

    tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    if (tokens.total_tokens + maximum_output_token) > max_context_size:
        # Compaction was not sufficient. Truncating messages.
        truncated_res = truncate_messages_to_fit_context(
            messages=messages,
            max_context_size=max_context_size,
            maximum_output_token=maximum_output_token,
            count_tokens_fn=llm.count_tokens,
        )
        metadata["truncations"] = [t.model_dump() for t in truncated_res.truncations]
        messages = truncated_res.truncated_messages

        # recount after truncation
        tokens = llm.count_tokens(messages=messages, tools=tools)  # type: ignore
    else:
        metadata["truncations"] = []

    return ContextWindowLimiterOutput(
        events=events,
        messages=messages,
        metadata=metadata,
        max_context_size=max_context_size,
        maximum_output_token=maximum_output_token,
        tokens=tokens,
        conversation_history_compacted=conversation_history_compacted,
    )
