import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from rich.console import Console

from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.runbooks import RunbookCatalog
from holmes.utils.global_instructions import generate_runbooks_args


class PromptComponent(str, Enum):
    # User prompt components
    FILES = "files"
    TODOWRITE_REMINDER = "todowrite_reminder"
    TIME_RUNBOOKS = "time_runbooks"
    # System prompt components
    INTRO = "intro"
    TODOWRITE_INSTRUCTIONS = "todowrite_instructions"
    AI_SAFETY = "ai_safety"
    TOOLSET_INSTRUCTIONS = "toolset_instructions"
    PERMISSION_ERRORS = "permission_errors"
    GENERAL_INSTRUCTIONS = "general_instructions"
    STYLE_GUIDE = "style_guide"


def is_prompt_enabled(component: PromptComponent) -> bool:
    """
    Check if a specific prompt component is enabled.

    Environment variable: ENABLED_PROMPTS
    - If not set: all prompts are ENABLED (production default)
    - If set to "none": all prompts are disabled
    - Comma-separated names (e.g., "files,ai_safety,time_runbooks")
    """
    enabled_prompts = os.environ.get("ENABLED_PROMPTS", "")

    if not enabled_prompts:
        return True  # Default: all enabled
    if enabled_prompts.lower() == "none":
        return False

    enabled_names = [x.strip().lower() for x in enabled_prompts.split(",")]
    return component.value in enabled_names


# System prompt components list
SYSTEM_PROMPT_COMPONENTS = [
    PromptComponent.INTRO,
    PromptComponent.TODOWRITE_INSTRUCTIONS,
    PromptComponent.AI_SAFETY,
    PromptComponent.TOOLSET_INSTRUCTIONS,
    PromptComponent.PERMISSION_ERRORS,
    PromptComponent.GENERAL_INSTRUCTIONS,
    PromptComponent.STYLE_GUIDE,
]


def is_any_system_prompt_component_enabled() -> bool:
    """Check if any system prompt component is enabled."""
    return any(is_prompt_enabled(c) for c in SYSTEM_PROMPT_COMPONENTS)


def append_file_to_user_prompt(user_prompt: str, file_path: Path) -> str:
    with file_path.open("r") as f:
        user_prompt += f"\n\n<attached-file path='{file_path.absolute()}'>\n{f.read()}\n</attached-file>"

    return user_prompt


def append_all_files_to_user_prompt(
    console: Console, user_prompt: str, file_paths: Optional[List[Path]]
) -> str:
    if not file_paths:
        return user_prompt

    for file_path in file_paths:
        console.print(f"[bold yellow]Adding file {file_path} to context[/bold yellow]")
        user_prompt = append_file_to_user_prompt(user_prompt, file_path)

    return user_prompt


def get_tasks_management_system_reminder() -> str:
    return (
        "\n\n<system-reminder>\nIMPORTANT: You have access to the TodoWrite tool. It creates a TodoList, in order to track progress. It's very important. You MUST use it:\n1. FIRST: Ask your self which sub problems you need to solve in order to answer the question."
        "Do this, BEFORE any other tools\n2. "
        "AFTER EVERY TOOL CALL: If required, update the TodoList\n3. "
        "\n\nFAILURE TO UPDATE TodoList = INCOMPLETE INVESTIGATION\n\n"
        "Example flow:\n- Think and divide to sub problems → create TodoList → Perform each task on the list → Update list → Verify your solution\n</system-reminder>"
    )


def _has_content(value: Optional[str]) -> bool:
    """
    Check if the value is a non-empty string and not None.
    """
    return bool(value and isinstance(value, str) and value.strip())


def _should_enable_runbooks(context: Dict[str, str]) -> bool:
    return any(
        (
            _has_content(context.get("runbook_catalog")),
            _has_content(context.get("custom_instructions")),
            _has_content(context.get("global_instructions")),
        )
    )


def generate_user_prompt(
    user_prompt: str,
    context: Dict[str, str],
) -> str:
    runbooks_enabled = _should_enable_runbooks(context)

    return load_and_render_prompt(
        "builtin://base_user_prompt.jinja2",
        context={
            "user_prompt": user_prompt,
            "runbooks_enabled": runbooks_enabled,
            **context,
        },
    )


def build_initial_ask_messages(
    console: Console,
    initial_user_prompt: str,
    file_paths: Optional[List[Path]],
    tool_executor: Any,  # ToolExecutor type
    runbooks: Union[RunbookCatalog, Dict, None] = None,
    system_prompt_additions: Optional[str] = None,
) -> List[Dict]:
    """Build the initial messages for the AI call.

    Args:
        console: Rich console for output
        initial_user_prompt: The user's prompt
        file_paths: Optional list of files to include
        tool_executor: The tool executor with available toolsets
        runbooks: Optional runbook catalog
        system_prompt_additions: Optional additional system prompt content
    """
    # [PROMPT #1] Append files to user prompt
    if is_prompt_enabled(PromptComponent.FILES):
        user_prompt_with_files = append_all_files_to_user_prompt(
            console, initial_user_prompt, file_paths
        )
    else:
        user_prompt_with_files = initial_user_prompt

    # [PROMPT #2] TodoWrite reminder (added to user prompt)
    if is_prompt_enabled(PromptComponent.TODOWRITE_REMINDER):
        user_prompt_with_files += get_tasks_management_system_reminder()

    # [PROMPT #3] Runbook context + time period text (added to user prompt)
    if is_prompt_enabled(PromptComponent.TIME_RUNBOOKS):
        runbooks_ctx = generate_runbooks_args(
            runbook_catalog=runbooks,  # type: ignore
        )
        user_prompt_with_files = generate_user_prompt(
            user_prompt_with_files,
            runbooks_ctx,
        )

    messages = []

    # [PROMPT #4] System prompt from generic_ask.jinja2 template
    # System prompt is sent if ANY of its components are enabled
    if is_any_system_prompt_component_enabled():
        system_prompt_template = "builtin://generic_ask.jinja2"
        template_context = {
            "toolsets": tool_executor.toolsets,
            "runbooks_enabled": True if runbooks else False,
            "system_prompt_additions": system_prompt_additions or "",
            # Pass individual component flags to templates
            "intro_enabled": is_prompt_enabled(PromptComponent.INTRO),
            "todowrite_enabled": is_prompt_enabled(PromptComponent.TODOWRITE_INSTRUCTIONS),
            "ai_safety_enabled": is_prompt_enabled(PromptComponent.AI_SAFETY),
            "toolset_instructions_enabled": is_prompt_enabled(PromptComponent.TOOLSET_INSTRUCTIONS),
            "permission_errors_enabled": is_prompt_enabled(PromptComponent.PERMISSION_ERRORS),
            "general_instructions_enabled": is_prompt_enabled(PromptComponent.GENERAL_INSTRUCTIONS),
            "style_guide_enabled": is_prompt_enabled(PromptComponent.STYLE_GUIDE),
        }
        system_prompt_rendered = load_and_render_prompt(
            system_prompt_template, template_context
        )
        messages.append({"role": "system", "content": system_prompt_rendered})

    messages.append({"role": "user", "content": user_prompt_with_files})

    return messages
