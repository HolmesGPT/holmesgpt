"""
Memory limit utilities for tool subprocess execution.
"""

import logging

from holmes.common.env_vars import TOOL_MEMORY_LIMIT_MB

logger = logging.getLogger(__name__)

# Maximum number of lines to keep from OOM crash output.
# The first few lines contain the error message; the rest is typically
# goroutine stack dumps (Go) or core-dump noise that wastes tokens.
OOM_OUTPUT_MAX_LINES = 10


def get_ulimit_prefix() -> str:
    """
    Get the ulimit command prefix for memory protection.

    Returns a shell command prefix that sets virtual memory limit.
    The '|| true' ensures we continue even if ulimit is not supported.
    """
    memory_limit_kb = TOOL_MEMORY_LIMIT_MB * 1024
    return f"ulimit -v {memory_limit_kb} 2>/dev/null || true; "


def _truncate_oom_output(output: str) -> str:
    """Truncate OOM crash output to just the error summary.

    OOM crashes (especially from Go programs like kubectl) produce huge
    goroutine stack dumps that are useless for the LLM and waste tokens.
    Keep only the first few lines which contain the actual error message.
    """
    if not output:
        return output

    lines = output.splitlines()
    if len(lines) <= OOM_OUTPUT_MAX_LINES:
        return output

    truncated_lines = lines[:OOM_OUTPUT_MAX_LINES]
    omitted = len(lines) - OOM_OUTPUT_MAX_LINES
    truncated_lines.append(f"[... {omitted} lines of stack trace omitted ...]")
    return "\n".join(truncated_lines)


def check_oom_and_append_hint(output: str, return_code: int) -> str:
    """
    Check if a command was OOM killed and append a helpful hint.

    Args:
        output: The command output
        return_code: The command's return code

    Returns:
        Output with OOM hint appended if OOM was detected
    """
    # Common OOM indicators:
    # - Return code 137 (128 + 9 = SIGKILL, commonly OOM)
    # - Return code -9 (SIGKILL on some systems)
    # - "Killed" in output (Linux OOM killer message)
    # - "MemoryError" (Python)
    # - "Cannot allocate memory" (various tools)
    # - "out of memory" (Go runtime: "runtime: out of memory", "fatal error: out of memory")
    #
    # Note: String-based detection only triggers on non-zero exit codes to avoid
    # false positives when commands succeed but output contains OOM-related text
    # (e.g., kubectl describing a pod that was OOMKilled).
    has_oom_strings = (
        "Killed" in output
        or "MemoryError" in output
        or "Cannot allocate memory" in output
        or "bad_alloc" in output
        or "out of memory" in output
    )
    is_oom = return_code in (137, -9) or (return_code != 0 and has_oom_strings)

    if is_oom:
        hint = (
            f"[OOM] Command exceeded the memory limit ({TOOL_MEMORY_LIMIT_MB} MB). "
            f"This is normal and expected — Holmes enforces memory limits by design to stay within resource "
            f"budgets. This is NOT an error or bug.\n"
            f"Note to agent: Retry the query with filters to reduce memory usage — for example, filter by "
            f"namespace, label selector, specific resource name, or use a smaller time range. "
            f"Do NOT tell the user that something is broken or that there is an operational concern. "
            f"If you are unable to get results even after narrowing your queries, you may suggest that the "
            f"user increase the memory limit by setting the TOOL_MEMORY_LIMIT_MB environment variable."
        )
        if output:
            return hint + "\n\n" + _truncate_oom_output(output)
        return hint

    return output
