from typing import Any, Dict, List, Optional, Union

from holmes.config import Config
from holmes.core.prompt import (
    PromptComponent,
    build_prompts,
)
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.plugins.skills.skill_loader import SkillCatalog
from holmes.utils.global_instructions import (
    Instructions,
)


def add_or_update_system_prompt(
    conversation_history: List[Dict[str, Any]],
    system_prompt: Optional[str],
):
    """Ensure HolmesGPT's generated system prompt occupies position 0.

    HolmesGPT owns its system prompt and regenerates it every turn, so we always
    install it at the head of the conversation: overwrite an existing leading
    system message (e.g. a relay-supplied content-less stub) or insert one when
    the history doesn't start with a system message. Non-leading system messages
    (e.g. the marker that compaction appends to the end of the history) are left
    untouched. A None system_prompt (ENABLED_PROMPTS=none) is a deliberate no-op.
    """
    if system_prompt is None:
        return conversation_history

    if not conversation_history:
        conversation_history.append({"role": "system", "content": system_prompt})
    elif conversation_history[0].get("role") == "system":
        conversation_history[0]["content"] = system_prompt
    else:
        conversation_history.insert(0, {"role": "system", "content": system_prompt})

    return conversation_history


def build_chat_messages(
    ask: str,
    conversation_history: Optional[List[Dict[str, str]]],
    ai: ToolCallingLLM,
    config: Config,
    global_instructions: Optional[Instructions] = None,
    additional_system_prompt: Optional[str] = None,
    skills: Optional[SkillCatalog] = None,
    images: Optional[List[Union[str, Dict[str, Any]]]] = None,
    prompt_component_overrides: Optional[Dict[PromptComponent, bool]] = None,
) -> List[dict]:
    """Build messages for general chat conversation.

    Expects conversation_history in OpenAI format (system message first).
    For new conversations, creates system prompt via build_system_prompt.
    For existing conversations, updates the system prompt.

    Context window management (compaction, spill-to-disk) is handled by
    call_stream() -> compact_if_necessary(), not here.
    See docs/reference/context-management.md.
    """

    system_prompt, user_content = build_prompts(
        toolsets=ai.tool_executor.toolsets,
        user_prompt=ask,
        skills=skills,
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

    return conversation_history  # type: ignore
