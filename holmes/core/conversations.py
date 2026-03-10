from typing import Any, Dict, List, Optional, Union

import sentry_sdk

from holmes.config import Config
from holmes.core.prompt import (
    PromptComponent,
    build_prompts,
)
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.plugins.runbooks import RunbookCatalog
from holmes.utils.global_instructions import (
    Instructions,
)

DEFAULT_TOOL_SIZE = 10000


@sentry_sdk.trace
def calculate_tool_size(
    ai: ToolCallingLLM, messages_without_tools: list[dict], number_of_tools: int
) -> int:
    if number_of_tools == 0:
        return DEFAULT_TOOL_SIZE

    context_window = ai.llm.get_context_window_size()
    tokens = ai.llm.count_tokens(messages_without_tools)
    message_size_without_tools = tokens.total_tokens
    maximum_output_token = ai.llm.get_maximum_output_token()

    tool_size = min(
        DEFAULT_TOOL_SIZE,
        int(
            (context_window - message_size_without_tools - maximum_output_token)
            / number_of_tools
        ),
    )
    return tool_size


def truncate_tool_messages(conversation_history: list, tool_size: int) -> None:
    for message in conversation_history:
        if message.get("role") == "tool":
            message["content"] = message["content"][:tool_size]


def add_or_update_system_prompt(
    conversation_history: List[Dict[str, Any]],
    system_prompt: Optional[str],
):
    """Add or replace the system prompt in conversation history.

    Only replaces an existing system prompt if it's the first message.
    Otherwise inserts at position 0 if no system message exists.
    """
    if system_prompt is None:
        return conversation_history

    if not conversation_history:
        conversation_history.append({"role": "system", "content": system_prompt})
    elif conversation_history[0]["role"] == "system":
        conversation_history[0]["content"] = system_prompt
    else:
        existing_system_prompt = next(
            (
                message
                for message in conversation_history
                if message.get("role") == "system"
            ),
            None,
        )
        if not existing_system_prompt:
            conversation_history.insert(0, {"role": "system", "content": system_prompt})

    return conversation_history


def build_chat_messages(
    ask: str,
    conversation_history: Optional[List[Dict[str, str]]],
    ai: ToolCallingLLM,
    config: Config,
    global_instructions: Optional[Instructions] = None,
    additional_system_prompt: Optional[str] = None,
    runbooks: Optional[RunbookCatalog] = None,
    images: Optional[List[Union[str, Dict[str, Any]]]] = None,
    prompt_component_overrides: Optional[Dict[PromptComponent, bool]] = None,
) -> List[dict]:
    """Build messages for general chat conversation, truncating tool outputs to fit context window.

    Expects conversation_history in OpenAI format (system message first).
    For new conversations, creates system prompt via build_system_prompt.
    For existing conversations, updates the system prompt and truncates tool outputs as needed.
    """

    system_prompt, user_content = build_prompts(
        toolsets=ai.tool_executor.toolsets,
        user_prompt=ask,
        runbooks=runbooks,
        global_instructions=global_instructions,
        system_prompt_additions=additional_system_prompt,
        cluster_name=config.cluster_name,
        ask_user_enabled=False,
        file_paths=None,
        include_todowrite_reminder=False,
        images=images,
        prompt_component_overrides=prompt_component_overrides,
    )

    if not conversation_history:
        conversation_history = []
    else:
        conversation_history = conversation_history.copy()
    conversation_history = add_or_update_system_prompt(
        conversation_history, system_prompt
    )

    conversation_history.append({"role": "user", "content": user_content})  # type: ignore

    number_of_tools = len(
        [message for message in conversation_history if message.get("role") == "tool"]  # type: ignore
    )
    if number_of_tools == 0:
        return conversation_history  # type: ignore

    conversation_history_without_tools = [
        message
        for message in conversation_history  # type: ignore
        if message.get("role") != "tool"  # type: ignore
    ]

    tool_size = calculate_tool_size(
        ai, conversation_history_without_tools, number_of_tools
    )
    truncate_tool_messages(conversation_history, tool_size)  # type: ignore
    return conversation_history  # type: ignore
