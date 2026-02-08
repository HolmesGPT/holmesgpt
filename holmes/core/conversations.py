from typing import Any, Dict, List, Optional, Union

import sentry_sdk

from holmes.config import Config
from holmes.core.models import (
    IssueChatRequest,
    ToolCallConversationResult,
)
from holmes.core.prompt import (
    build_prompts,
    generate_user_prompt,
)
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.runbooks import RunbookCatalog
from holmes.utils.global_instructions import (
    Instructions,
    generate_runbooks_args,
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


def truncate_tool_outputs(
    tools: list, tool_size: int
) -> list[ToolCallConversationResult]:
    return [
        ToolCallConversationResult(
            name=tool.name,
            description=tool.description,
            output=tool.output[:tool_size],
        )
        for tool in tools
    ]


def truncate_tool_messages(conversation_history: list, tool_size: int) -> None:
    for message in conversation_history:
        if message.get("role") == "tool":
            message["content"] = message["content"][:tool_size]


def build_issue_chat_messages(
    issue_chat_request: IssueChatRequest,
    ai: ToolCallingLLM,
    config: Config,
    global_instructions: Optional[Instructions] = None,
    runbooks: Optional[RunbookCatalog] = None,
):
    """Build messages for issue conversation, truncating tool outputs to fit context window.

    Expects conversation_history in OpenAI format (system message first).
    For new conversations, creates system prompt from generic_ask_for_issue_conversation.jinja2.
    For existing conversations, updates the system prompt and truncates tool outputs as needed.
    """
    template_path = "builtin://generic_ask_for_issue_conversation.jinja2"

    conversation_history = issue_chat_request.conversation_history
    user_prompt = issue_chat_request.ask
    investigation_analysis = issue_chat_request.investigation_result.result
    tools_for_investigation = issue_chat_request.investigation_result.tools

    if not conversation_history or len(conversation_history) == 0:
        runbooks_ctx = generate_runbooks_args(
            runbook_catalog=runbooks,
            global_instructions=global_instructions,
        )
        user_prompt = generate_user_prompt(
            user_prompt,
            runbooks_ctx,
        )

        number_of_tools_for_investigation = len(tools_for_investigation)  # type: ignore
        if number_of_tools_for_investigation == 0:
            system_prompt = load_and_render_prompt(
                template_path,
                {
                    "investigation": investigation_analysis,
                    "tools_called_for_investigation": tools_for_investigation,
                    "issue": issue_chat_request.issue_type,
                    "toolsets": ai.tool_executor.toolsets,
                    "cluster_name": config.cluster_name,
                    "runbooks_enabled": bool(runbooks),
                },
            )
            messages = [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ]
            return messages

        template_context_without_tools = {
            "investigation": investigation_analysis,
            "tools_called_for_investigation": None,
            "issue": issue_chat_request.issue_type,
            "toolsets": ai.tool_executor.toolsets,
            "cluster_name": config.cluster_name,
            "runbooks_enabled": bool(runbooks),
        }
        system_prompt_without_tools = load_and_render_prompt(
            template_path, template_context_without_tools
        )
        messages_without_tools = [
            {
                "role": "system",
                "content": system_prompt_without_tools,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ]
        tool_size = calculate_tool_size(
            ai, messages_without_tools, number_of_tools_for_investigation
        )

        truncated_template_context = {
            "investigation": investigation_analysis,
            "tools_called_for_investigation": truncate_tool_outputs(tools_for_investigation, tool_size),  # type: ignore
            "issue": issue_chat_request.issue_type,
            "toolsets": ai.tool_executor.toolsets,
            "cluster_name": config.cluster_name,
            "runbooks_enabled": bool(runbooks),
        }
        system_prompt_with_truncated_tools = load_and_render_prompt(
            template_path, truncated_template_context
        )
        return [
            {
                "role": "system",
                "content": system_prompt_with_truncated_tools,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ]

    runbooks_ctx = generate_runbooks_args(
        runbook_catalog=runbooks,
        global_instructions=global_instructions,
    )
    user_prompt = generate_user_prompt(
        user_prompt,
        runbooks_ctx,
    )

    conversation_history.append(
        {
            "role": "user",
            "content": user_prompt,
        }
    )
    number_of_tools = len(tools_for_investigation) + len(  # type: ignore
        [message for message in conversation_history if message.get("role") == "tool"]
    )

    if number_of_tools == 0:
        return conversation_history

    conversation_history_without_tools = [
        message for message in conversation_history if message.get("role") != "tool"
    ]
    template_context_without_tools = {
        "investigation": investigation_analysis,
        "tools_called_for_investigation": None,
        "issue": issue_chat_request.issue_type,
        "toolsets": ai.tool_executor.toolsets,
        "cluster_name": config.cluster_name,
        "runbooks_enabled": bool(runbooks),
    }
    system_prompt_without_tools = load_and_render_prompt(
        template_path, template_context_without_tools
    )
    conversation_history_without_tools[0]["content"] = system_prompt_without_tools

    tool_size = calculate_tool_size(
        ai, conversation_history_without_tools, number_of_tools
    )

    template_context = {
        "investigation": investigation_analysis,
        "tools_called_for_investigation": truncate_tool_outputs(tools_for_investigation, tool_size),  # type: ignore
        "issue": issue_chat_request.issue_type,
        "toolsets": ai.tool_executor.toolsets,
        "cluster_name": config.cluster_name,
        "runbooks_enabled": bool(runbooks),
    }
    system_prompt_with_truncated_tools = load_and_render_prompt(
        template_path, template_context
    )
    conversation_history[0]["content"] = system_prompt_with_truncated_tools

    truncate_tool_messages(conversation_history, tool_size)

    return conversation_history


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
    )

    if not conversation_history:
        conversation_history = []
    else:
        conversation_history = conversation_history.copy()
    conversation_history = add_or_update_system_prompt(conversation_history, system_prompt)

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
