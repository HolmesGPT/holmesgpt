"""
Single tool result size limiter — spills oversized results to disk.

For an overview of all context management mechanisms, see:
docs/reference/context-management.md
"""

import logging
import time
from pathlib import Path
from typing import Optional

from holmes.common.env_vars import load_bool
from holmes.core.llm import LLM
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResultStatus
from holmes.core.tools_utils.filesystem_result_storage import save_images, save_large_result
from holmes.utils import sentry_helper


def get_pct_token_count(percent_of_total_context_window: float, llm: LLM) -> int:
    context_window_size = llm.get_context_window_size()

    if 0 < percent_of_total_context_window and percent_of_total_context_window <= 100:
        return int(context_window_size * percent_of_total_context_window // 100)
    else:
        return context_window_size


def spill_oversized_tool_result(
    tool_call_result: ToolCallResult,
    llm: LLM,
    tool_results_dir: Optional[Path] = None,
) -> int:
    """
    Handle tool results that exceed the context window limit.

    If tool_results_dir is provided and filesystem storage is enabled, saves large
    results to the directory and returns a pointer message to the LLM. Otherwise,
    falls back to dropping the data with an error message.

    Returns the token count of the original message.
    """
    t0 = time.monotonic()
    message = tool_call_result.to_llm_message()
    messages_token = llm.count_tokens(messages=[message]).total_tokens
    max_tokens_allowed = llm.get_max_token_count_for_single_tool()
    logging.debug(f"spill_oversized_tool_result: count_tokens took {(time.monotonic() - t0) * 1000:.1f}ms for {tool_call_result.tool_name} ({messages_token} tokens)")

    if tool_call_result.result.status != StructuredToolResultStatus.SUCCESS:
        return messages_token
    if messages_token <= max_tokens_allowed:
        return messages_token

    # Guard against infinite loop: if read_image_file returns an oversized image,
    # don't save it and instruct "use read_image_file" again — that would cause the
    # LLM to re-read the same oversized image repeatedly until max_steps is exhausted.
    if tool_call_result.tool_name == "read_image_file":
        tool_call_result.result.status = StructuredToolResultStatus.ERROR
        tool_call_result.result.data = None
        tool_call_result.result.images = None
        tool_call_result.result.error = (
            f"Image too large to display inline ({messages_token} tokens, "
            f"max {max_tokens_allowed}). Try a smaller image or use a different approach."
        )
        return messages_token

    size_info = f"The tool call result is too large to return: {messages_token}/{max_tokens_allowed} tokens.\n"

    # Try filesystem storage if a directory is provided and storage is enabled
    file_path = None
    filesystem_data = ""
    image_paths: list[str] = []
    if tool_results_dir and load_bool("HOLMES_TOOL_RESULT_STORAGE_ENABLED", True):
        filesystem_data, is_json = tool_call_result.result.stringify_data(compact=False)
        file_path = save_large_result(
            tool_results_dir=tool_results_dir,
            tool_name=tool_call_result.tool_name,
            tool_call_id=tool_call_result.tool_call_id,
            content=filesystem_data,
            is_json=is_json,
        )

    if file_path:
        # Save images to disk so the LLM can read them back via read_image_file
        if tool_call_result.result.images:
            image_paths = save_images(
                tool_results_dir=tool_results_dir,
                tool_name=tool_call_result.tool_name,
                tool_call_id=tool_call_result.tool_call_id,
                images=tool_call_result.result.images,
            )
        boilerplate = (
            f"{size_info}\n"
            f"Saved to: {file_path}\n"
            f"Use `cat {file_path}` to read it (pre-approved, no user approval needed). "
            f"You can pipe the output into any command to filter, for example: "
            f"`cat {file_path} | jq '.field'`, `cat {file_path} | grep -oP 'pattern'`, etc.\n"
        )
        if image_paths:
            boilerplate += (
                f"\nImages saved to disk ({len(image_paths)} file(s)):\n"
            )
            for img_path in image_paths:
                boilerplate += f"  - {img_path}\n"
            boilerplate += (
                "Use read_image_file to view any of these images.\n"
            )
        boilerplate += "\nPreview:\n"
        # Allocate remaining char budget to the preview so the final string fits the context window
        chars_per_token = 4
        safety_margin_chars_per_token = chars_per_token / 2
        max_chars = max_tokens_allowed * safety_margin_chars_per_token
        preview_budget = int(max(0, max_chars - len(boilerplate)))
        preview = filesystem_data[:preview_budget]
        tool_call_result.result.data = f"{boilerplate}{preview}"
        # Clear images from the result since they're now on disk
        tool_call_result.result.images = None
        logging.info(
            f"Large tool result ({messages_token} tokens) saved to {file_path}"
            + (f" with {len(image_paths)} image(s)" if image_paths else "")
        )
    else:
        tool_call_result.result.status = StructuredToolResultStatus.ERROR
        tool_call_result.result.data = None
        tool_call_result.result.images = None
        tool_call_result.result.error = (
            f"{size_info}\n"
            f"Try to repeat the query but proactively narrow down the result "
            f"so that the tool answer fits within the allowed number of tokens."
        )
        # Only report to Sentry when data is dropped (filesystem storage unavailable/failed)
        sentry_helper.capture_toolcall_contains_too_many_tokens(
            tool_call_result, messages_token, max_tokens_allowed
        )
    return messages_token
