import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.runbooks import RunbookCatalog
from holmes.utils.global_instructions import Instructions, generate_runbooks_args


class PromptComponent(str, Enum):
    # User prompt components
    FILES = "files"
    TODOWRITE_REMINDER = "todowrite_reminder"
    TIME_RUNBOOKS = "time_runbooks"
    # System prompt components
    INTRO = "intro"
    ASK_USER = "ask_user"
    TODOWRITE_INSTRUCTIONS = "todowrite_instructions"
    AI_SAFETY = "ai_safety"
    TOOLSET_INSTRUCTIONS = "toolset_instructions"
    PERMISSION_ERRORS = "permission_errors"
    GENERAL_INSTRUCTIONS = "general_instructions"
    STYLE_GUIDE = "style_guide"
    CLUSTER_NAME = "cluster_name"
    SYSTEM_PROMPT_ADDITIONS = "system_prompt_additions"


class InvalidImageDictError(ValueError):
    """Raised when an image dict is missing required keys or is malformed."""

    def __init__(self, provided_keys: List[str]):
        self.provided_keys = provided_keys
        super().__init__(
            f"Image dict must contain a 'url' key. Got keys: {provided_keys}"
        )


def build_vision_content(
    text: str, images: List[Union[str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Build content array for vision models with text and images.

    Args:
        text: The text content
        images: List of images, each can be:
            - str: URL or base64 data URI
            - dict: Object with 'url' (required), 'detail', and 'format' fields

    Returns:
        List of content items in OpenAI vision format

    Raises:
        InvalidImageDictError: If an image dict is missing the 'url' key
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    for image_item in images:
        if isinstance(image_item, str):
            content.append({"type": "image_url", "image_url": {"url": image_item}})
        else:
            if "url" not in image_item:
                raise InvalidImageDictError(list(image_item.keys()))
            image_url_obj: Dict[str, Any] = {"url": image_item["url"]}
            if "detail" in image_item:
                image_url_obj["detail"] = image_item["detail"]
            if "format" in image_item:
                image_url_obj["format"] = image_item["format"]
            content.append({"type": "image_url", "image_url": image_url_obj})
    return content


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


def append_file_to_user_prompt(user_prompt: str, file_path: Path) -> str:
    with file_path.open("r") as f:
        user_prompt += f"\n\n<attached-file path='{file_path.absolute()}'>\n{f.read()}\n</attached-file>"

    return user_prompt


def append_all_files_to_user_prompt(
    user_prompt: str,
    file_paths: Optional[List[Path]],
) -> str:
    if not file_paths:
        return user_prompt

    for file_path in file_paths:
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


def build_system_prompt(
    toolsets: List[Any],
    runbooks: Optional[RunbookCatalog],
    system_prompt_additions: Optional[str],
    cluster_name: Optional[str],
    ask_user_enabled: bool,
) -> Optional[str]:
    """
    Build the system prompt for both CLI and server modes.
    Returns None if the rendered prompt is empty.
    """
    toolset_instructions_enabled = is_prompt_enabled(PromptComponent.TOOLSET_INSTRUCTIONS)

    template_context = {
        "intro_enabled": is_prompt_enabled(PromptComponent.INTRO),
        "ask_user_enabled": ask_user_enabled and is_prompt_enabled(PromptComponent.ASK_USER),
        "todowrite_enabled": is_prompt_enabled(PromptComponent.TODOWRITE_INSTRUCTIONS),
        "ai_safety_enabled": is_prompt_enabled(PromptComponent.AI_SAFETY),
        "toolset_instructions_enabled": toolset_instructions_enabled,
        "permission_errors_enabled": is_prompt_enabled(PromptComponent.PERMISSION_ERRORS),
        "general_instructions_enabled": is_prompt_enabled(PromptComponent.GENERAL_INSTRUCTIONS),
        "style_guide_enabled": is_prompt_enabled(PromptComponent.STYLE_GUIDE),
        "runbooks_enabled": bool(runbooks) and is_prompt_enabled(PromptComponent.TIME_RUNBOOKS),
        "cluster_name": cluster_name if is_prompt_enabled(PromptComponent.CLUSTER_NAME) else None,
        "toolsets": toolsets if toolset_instructions_enabled else [],
        "system_prompt_additions": system_prompt_additions if is_prompt_enabled(PromptComponent.SYSTEM_PROMPT_ADDITIONS) else "",
    }

    result = load_and_render_prompt("builtin://generic_ask.jinja2", template_context)
    return result if result and result.strip() else None


UserPromptContent = Union[str, List[Dict[str, Any]]]


def build_user_prompt(
    user_prompt: str,
    runbooks: Optional[RunbookCatalog],
    global_instructions: Optional[Instructions],
    file_paths: Optional[List[Path]],
    include_todowrite_reminder: bool,
    images: Optional[List[Union[str, Dict[str, Any]]]],
) -> UserPromptContent:
    """Build the user prompt with all enrichments.

    Returns:
        Either a string or a list of content dicts (for vision models with images).
    """
    # Handle file attachments (CLI mode passes files, server mode passes None)
    if file_paths and is_prompt_enabled(PromptComponent.FILES):
        user_prompt = append_all_files_to_user_prompt(user_prompt, file_paths)

    # Handle todowrite reminder (CLI mode passes True, server mode passes False)
    if include_todowrite_reminder and is_prompt_enabled(PromptComponent.TODOWRITE_REMINDER):
        user_prompt += get_tasks_management_system_reminder()

    # Enrich with runbooks (if TIME_RUNBOOKS component is enabled)
    if is_prompt_enabled(PromptComponent.TIME_RUNBOOKS):
        runbooks_ctx = generate_runbooks_args(
            runbook_catalog=runbooks,
            global_instructions=global_instructions,
        )
        user_prompt = generate_user_prompt(user_prompt, runbooks_ctx)

    # Handle images (server mode may pass images, CLI mode passes None)
    if images:
        return build_vision_content(user_prompt, images)
    return user_prompt


def build_prompts(
    toolsets: List[Any],
    user_prompt: str,
    runbooks: Optional[RunbookCatalog],
    global_instructions: Optional[Instructions],
    system_prompt_additions: Optional[str],
    cluster_name: Optional[str],
    ask_user_enabled: bool,
    file_paths: Optional[List[Path]],
    include_todowrite_reminder: bool,
    images: Optional[List[Union[str, Dict[str, Any]]]],
) -> Tuple[Optional[str], UserPromptContent]:
    """Build both system and user prompts."""
    system_prompt = build_system_prompt(
        toolsets=toolsets,
        runbooks=runbooks,
        system_prompt_additions=system_prompt_additions,
        cluster_name=cluster_name,
        ask_user_enabled=ask_user_enabled,
    )
    user_content = build_user_prompt(
        user_prompt=user_prompt,
        runbooks=runbooks,
        global_instructions=global_instructions,
        file_paths=file_paths,
        include_todowrite_reminder=include_todowrite_reminder,
        images=images,
    )
    return system_prompt, user_content


def build_initial_ask_messages(
    initial_user_prompt: str,
    file_paths: Optional[List[Path]],
    tool_executor: Any,  # ToolExecutor type
    runbooks: Optional[RunbookCatalog] = None,
    system_prompt_additions: Optional[str] = None,
    global_instructions: Optional[Instructions] = None,
    cluster_name: Optional[str] = None,
) -> List[Dict]:
    """Build the initial messages for the CLI ask command."""
    system_prompt, user_prompt = build_prompts(
        toolsets=tool_executor.toolsets,
        user_prompt=initial_user_prompt,
        runbooks=runbooks,
        global_instructions=global_instructions,
        system_prompt_additions=system_prompt_additions,
        cluster_name=cluster_name,
        ask_user_enabled=True,
        file_paths=file_paths,
        include_todowrite_reminder=True,
        images=None,
    )

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages
