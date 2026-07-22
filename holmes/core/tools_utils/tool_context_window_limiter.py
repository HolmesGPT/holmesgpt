"""
Tool-result context management — spills oversized results to disk and evicts
stale results from long conversations.

For an overview of all context management mechanisms, see:
docs/reference/context-management.md
"""

import logging
import re
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from holmes.common.env_vars import (
    ENABLE_TOOL_RESULT_EVICTION,
    TOOL_RESULT_EVICTION_MAX_AGE_TURNS,
    TOOL_RESULT_EVICTION_MIN_TOKENS,
    load_bool,
)
from holmes.core.llm import LLM
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResultStatus
from holmes.core.tools_utils.filesystem_result_storage import save_images, save_large_result
from holmes.utils import sentry_helper

# Rough chars-per-token estimate, matching spill_oversized_tool_result. Eviction
# uses a character-length heuristic instead of the tokenizer so it stays cheap
# and deterministic when run on every agentic iteration.
_CHARS_PER_TOKEN = 4

# Every eviction stub starts with this marker so eviction is idempotent: a stub
# is never counted as a fresh tool result and never re-evicted / re-spilled.
EVICTED_TOOL_RESULT_MARKER = "[Tool result evicted to save context]"

# Matches the "Saved to: <path>" line written by spill_oversized_tool_result, so
# an already-spilled result can be re-pointed at its existing file instead of
# writing its (bounded) preview to disk a second time.
_SAVED_TO_RE = re.compile(r"^Saved to: (.+)$", re.MULTILINE)


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


class ToolResultEvictionResult(BaseModel):
    """Outcome of an evict_stale_tool_results() pass."""

    messages: list[dict]
    num_evicted: int
    estimated_tokens_freed: int


def _build_eviction_stub(tool_name: str, file_path: str, approx_tokens: int) -> str:
    """The short in-conversation replacement for an evicted tool result.

    Keep this a single `cat` instruction (no pipe/filter examples): an evicted
    result already fit in the context window when it was first returned, so the
    model can safely re-read the whole file, and a single command is far more
    reliable to issue than a multi-stage pipeline.
    """
    return (
        f"{EVICTED_TOOL_RESULT_MARKER} The full `{tool_name}` output "
        f"(~{approx_tokens} tokens) was read earlier in this investigation and "
        f"has been moved out of the live conversation to save context.\n"
        f"Saved to: {file_path}\n"
        f"If you need its details again, re-read the whole file by running "
        f"exactly `cat {file_path}` (pre-approved, no user approval needed)."
    )


def evict_stale_tool_results(
    messages: list[dict],
    tool_results_dir: Optional[Path],
    max_age_turns: int = TOOL_RESULT_EVICTION_MAX_AGE_TURNS,
    min_tokens: int = TOOL_RESULT_EVICTION_MIN_TOKENS,
    enabled: Optional[bool] = None,
) -> ToolResultEvictionResult:
    """Deterministically evict tool results older than ``max_age_turns``.

    Mirrors Anthropic's "context editing" pattern: once a tool result has been
    read and reasoned over, its raw payload is dead weight that is otherwise
    re-sent on every agentic iteration. This replaces such results with a short
    stub plus a spill-to-disk pointer the model can ``cat`` if it needs the
    detail again. It is deterministic and free (no LLM call).

    A tool result's *age* is the number of assistant turns that came after it.
    The results from the most recent ``max_age_turns`` assistant turns are kept
    in full; anything older and larger than ``min_tokens`` is evicted. The
    messages list is mutated in place (and also returned).

    Requires ``tool_results_dir`` (and filesystem storage to be enabled) so the
    full result survives on disk for re-reading — without it there is no safe
    way to evict, so the conversation is left untouched.
    """
    should_run = ENABLE_TOOL_RESULT_EVICTION if enabled is None else enabled
    result = ToolResultEvictionResult(
        messages=messages, num_evicted=0, estimated_tokens_freed=0
    )
    if not should_run or max_age_turns < 0:
        return result
    if not tool_results_dir or not load_bool("HOLMES_TOOL_RESULT_STORAGE_ENABLED", True):
        return result

    # Age of each message = number of assistant messages strictly after it.
    n = len(messages)
    assistants_after = [0] * n
    seen = 0
    for j in range(n - 1, -1, -1):
        assistants_after[j] = seen
        if messages[j].get("role") == "assistant":
            seen += 1

    min_chars = max(0, min_tokens) * _CHARS_PER_TOKEN
    num_evicted = 0
    tokens_freed = 0

    for j, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        if assistants_after[j] < max_age_turns:
            continue  # still within the "keep recent turns" window

        content = message.get("content")
        # Only plain-string tool outputs are evicted. Multimodal (image) results
        # are rare here and are already handled by spill_oversized_tool_result on
        # the way in, which turns oversized image results into string pointers.
        if not isinstance(content, str) or not content:
            continue
        if content.startswith(EVICTED_TOOL_RESULT_MARKER):
            continue  # already evicted — keep it stable for the prompt cache
        if len(content) < min_chars:
            continue  # too small to be worth stubbing + re-reading

        tool_name = message.get("name") or "tool"
        tool_call_id = message.get("tool_call_id") or ""
        approx_tokens = len(content) // _CHARS_PER_TOKEN

        # If this result was already spilled to disk and that file still exists,
        # re-point at it instead of writing its (bounded) preview to disk again.
        # SECURITY: only trust a "Saved to:" pointer that WE wrote — i.e. a
        # regular file directly under tool_results_dir whose name matches the
        # spill filename for this exact tool call. A tool result could otherwise
        # embed a forged "Saved to: /etc/..." line and get the model to cat an
        # arbitrary existing file. Anything else falls through to a fresh spill.
        file_path: Optional[str] = None
        existing = _SAVED_TO_RE.search(content)
        if existing:
            candidate = existing.group(1).strip()
            if candidate:
                # `candidate` is untrusted tool output — a malformed value (e.g.
                # an embedded NUL byte) can make resolve()/is_file() raise. Swallow
                # any such error and fall through to a fresh spill rather than
                # letting it abort the whole eviction pass.
                try:
                    candidate_path = Path(candidate).resolve()
                    safe_name = re.sub(r"[^\w\-]", "_", tool_name)
                    safe_id = re.sub(r"[^\w\-]", "_", tool_call_id)
                    expected_names = {
                        f"{safe_name}_{safe_id}.txt",
                        f"{safe_name}_{safe_id}.json",
                    }
                    if (
                        candidate_path.parent == tool_results_dir.resolve()
                        and candidate_path.name in expected_names
                        and candidate_path.is_file()
                    ):
                        file_path = str(candidate_path)
                except (OSError, ValueError, RuntimeError) as e:
                    logging.debug(
                        f"Ignoring malformed 'Saved to:' pointer during eviction: {e}"
                    )

        if not file_path:
            file_path = save_large_result(
                tool_results_dir=tool_results_dir,
                tool_name=tool_name,
                # Distinct id so we never clobber a same-turn spill file, which
                # holds the full (not preview-truncated) data.
                tool_call_id=f"{tool_call_id}__evicted",
                content=content,
                is_json=False,
            )
        if not file_path:
            logging.warning(
                f"Skipping eviction of {tool_name} result: filesystem storage failed"
            )
            continue

        stub = _build_eviction_stub(tool_name, file_path, approx_tokens)
        message["content"] = stub
        num_evicted += 1
        tokens_freed += max(0, approx_tokens - len(stub) // _CHARS_PER_TOKEN)

    if num_evicted:
        logging.info(
            f"Evicted {num_evicted} stale tool result(s) from conversation "
            f"history (~{tokens_freed} tokens freed, keeping last "
            f"{max_age_turns} turns)"
        )

    result.num_evicted = num_evicted
    result.estimated_tokens_freed = tokens_freed
    return result
