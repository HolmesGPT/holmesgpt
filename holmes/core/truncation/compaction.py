"""
LLM-based conversation history compaction — summarizes old messages to free context space.

For an overview of all context management mechanisms, see:
docs/reference/context-management.md
"""

import logging
from typing import Any, Optional

from litellm.types.utils import ModelResponse
from pydantic import BaseModel

from holmes.core.llm import LLM
from holmes.core.llm_usage import RequestStats
from holmes.plugins.prompts import load_and_render_prompt


class CompactionResult(BaseModel):
    """Result of conversation history compaction."""

    messages_after_compaction: list[dict]
    usage: Optional[RequestStats] = None


def strip_system_prompt(
    conversation_history: list[dict],
) -> tuple[list[dict], Optional[dict]]:
    if not conversation_history:
        return conversation_history, None
    first_message = conversation_history[0]
    if first_message and first_message.get("role") == "system":
        return conversation_history[1:], first_message
    return conversation_history[:], None


def find_last_user_prompt(conversation_history: list[dict]) -> Optional[dict]:
    if not conversation_history:
        return None
    last_user_prompt: Optional[dict] = None
    for message in conversation_history:
        if message.get("role") == "user":
            last_user_prompt = message
    return last_user_prompt


def _count_image_tokens_in_messages(messages: list[dict], llm: LLM) -> int:
    """Count total tokens used by image blocks across all messages."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        # Count tokens for a synthetic message containing only image blocks
        image_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "image_url"]
        if image_blocks:
            synthetic = {"role": "user", "content": image_blocks}
            total += llm.count_tokens(messages=[synthetic]).total_tokens
    return total


def _strip_images_for_compaction(messages: list[dict]) -> list[dict]:
    """Strip image_url blocks from messages, replacing with a count placeholder."""
    stripped: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            stripped.append(msg)
            continue
        new_content: list[dict[str, Any]] = []
        image_count = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                image_count += 1
            else:
                new_content.append(block)
        if image_count > 0:
            new_content.append({
                "type": "text",
                "text": f"[{image_count} image(s) were present but stripped from compaction]",
            })
        new_msg = dict(msg)
        new_msg["content"] = new_content
        new_msg.pop("token_count", None)
        stripped.append(new_msg)
    return stripped


def compact_conversation_history(
    original_conversation_history: list[dict], llm: LLM
) -> CompactionResult:
    """
    The compacted conversation history contains:
      1. Original system prompt, uncompacted (if present)
      2. Last user prompt, uncompacted (if present)
      3. Compacted conversation history (role=assistant)
      4. Compaction message (role=system)
    """
    conversation_history, system_prompt_message = strip_system_prompt(
        original_conversation_history
    )
    compaction_instructions = load_and_render_prompt(
        prompt="builtin://conversation_history_compaction.jinja2", context={}
    )

    # Decide whether to keep images in the compaction input.
    # Keep them if the conversation (with images) fits in the compaction LLM's
    # context window, so it can describe what was in them. Otherwise strip them.
    # Include instruction tokens in the budget since they are appended before the LLM call.
    context_window = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()
    instruction_tokens = llm.count_tokens(
        messages=[{"role": "user", "content": compaction_instructions}]
    ).total_tokens
    total_tokens = llm.count_tokens(messages=conversation_history).total_tokens
    image_tokens = _count_image_tokens_in_messages(conversation_history, llm)

    if image_tokens > 0 and (total_tokens + instruction_tokens + maximum_output_token) <= context_window:
        logging.info(
            f"Compaction: keeping {image_tokens} image tokens "
            f"(conversation fits in context window: {total_tokens} + {instruction_tokens} + {maximum_output_token} <= {context_window})"
        )
    elif image_tokens > 0:
        logging.info(
            f"Compaction: stripping {image_tokens} image tokens "
            f"(conversation would overflow: {total_tokens} + {instruction_tokens} + {maximum_output_token} > {context_window})"
        )
        conversation_history = _strip_images_for_compaction(conversation_history)

    conversation_history.append({"role": "user", "content": compaction_instructions})

    response: ModelResponse = llm.completion(
        messages=conversation_history, drop_params=True
    )  # type: ignore
    compaction_usage = RequestStats.from_response(response)

    response_message = None
    if (
        response
        and response.choices
        and response.choices[0]
        and response.choices[0].message  # type:ignore
    ):
        response_message = response.choices[0].message  # type:ignore
    else:
        logging.error(
            "Failed to compact conversation history. Unexpected LLM's response for compaction"
        )
        return CompactionResult(messages_after_compaction=original_conversation_history, usage=compaction_usage)

    compacted_conversation_history: list[dict] = []
    if system_prompt_message:
        compacted_conversation_history.append(system_prompt_message)

    last_user_prompt = find_last_user_prompt(original_conversation_history)
    if last_user_prompt:
        compacted_conversation_history.append(last_user_prompt)

    compacted_conversation_history.append(
        response_message.model_dump(
            exclude_defaults=True, exclude_unset=True, exclude_none=True
        )
    )

    compacted_conversation_history.append(
        {
            "role": "system",
            "content": "The conversation history has been compacted to preserve available space in the context window. Continue.",
        }
    )
    return CompactionResult(
        messages_after_compaction=compacted_conversation_history, usage=compaction_usage
    )
