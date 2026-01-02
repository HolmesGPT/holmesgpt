"""
Memory limit utilities for tool subprocess execution.
"""

import logging

from holmes.common.env_vars import TOOL_MEMORY_LIMIT_MB

logger = logging.getLogger(__name__)


def get_ulimit_prefix() -> str:
    """
    Get the ulimit command prefix for memory protection.

    Returns a shell command prefix that sets virtual memory limit.
    The '|| true' ensures we continue even if ulimit is not supported.
    """
    memory_limit_kb = TOOL_MEMORY_LIMIT_MB * 1024
    return f"ulimit -v {memory_limit_kb} || true; "


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
    is_oom = (
        return_code in (137, -9)
        or "Killed" in output
        or "MemoryError" in output
        or "Cannot allocate memory" in output
        or "bad_alloc" in output
    )

    if is_oom:
        hint = (
            f"\n\n[OOM] Command was killed due to memory limits (current limit: {TOOL_MEMORY_LIMIT_MB} MB). "
            f"Try querying the data differently to reduce memory usage - add filters to narrow the results, "
            f"use smaller time ranges, or try alternative tools that may be more memory-efficient. "
            f"If you cannot succeed with a modified query, you may recommend the user increase the limit "
            f"by setting the TOOL_MEMORY_LIMIT_MB environment variable (Tool memory limit, MB)."
        )
        return output + hint

    return output
