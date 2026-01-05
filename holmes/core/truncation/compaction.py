import logging
from typing import Optional

import litellm
from litellm.types.utils import ModelResponse

from holmes.core.llm import LLM
from holmes.plugins.prompts import load_and_render_prompt


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


def compact_conversation_history(
    original_conversation_history: list[dict], llm: LLM
) -> list[dict]:
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
    conversation_history.append({"role": "user", "content": compaction_instructions})

    # Set modify_params to handle providers like Anthropic that require tools
    # when conversation history contains tool calls
    original_modify_params = litellm.modify_params
    try:
        litellm.modify_params = True  # necessary when using anthropic
        response: ModelResponse = llm.completion(
            messages=conversation_history, drop_params=True
        )  # type: ignore
    finally:
        litellm.modify_params = original_modify_params
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
        return original_conversation_history

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
    return compacted_conversation_history
