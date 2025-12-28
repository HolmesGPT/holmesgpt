"""
Memory limit utilities for tool subprocess execution.

Provides functions to parse human-readable memory sizes and apply
ulimit-based memory protection to prevent OOM from crashing the main process.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# Environment variable for configuring memory limit for tool subprocesses
TOOL_MEMORY_LIMIT_ENV = "HOLMES_TOOL_MEMORY_LIMIT"
TOOL_MEMORY_LIMIT_DEFAULT = "500MB"


def parse_size_to_kb(size_str: str) -> int:
    """
    Parse a human-readable size string to kilobytes.

    Supports formats like: "2GB", "2gb", "2 GB", "2g", "1024MB", "2097152KB", "2097152".
    If no unit is specified, assumes kilobytes.

    Args:
        size_str: Human-readable size string

    Returns:
        Size in kilobytes (for use with ulimit -v)

    Raises:
        ValueError: If the size string cannot be parsed
    """
    size_str = size_str.strip().upper()

    # Match number (with optional decimal) and optional unit
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGT]?B?)?$", size_str)
    if not match:
        raise ValueError(f"Invalid size format: {size_str}")

    value = float(match.group(1))
    unit = match.group(2) or "K"  # Default to KB if no unit

    # Normalize unit (handle both "G" and "GB" style)
    unit = unit.rstrip("B") or "K"

    multipliers = {
        "K": 1,
        "M": 1024,
        "G": 1024 * 1024,
        "T": 1024 * 1024 * 1024,
    }

    if unit not in multipliers:
        raise ValueError(f"Unknown size unit: {unit}")

    return int(value * multipliers[unit])


def get_memory_limit_kb() -> int:
    """
    Get the configured memory limit in KB from environment variable.

    Returns the parsed memory limit, falling back to default if the env var
    is not set or has an invalid value.
    """
    memory_limit_str = os.environ.get(TOOL_MEMORY_LIMIT_ENV, TOOL_MEMORY_LIMIT_DEFAULT)
    try:
        return parse_size_to_kb(memory_limit_str)
    except ValueError as e:
        logger.warning(
            f"Invalid {TOOL_MEMORY_LIMIT_ENV}='{memory_limit_str}': {e}. "
            f"Using default: {TOOL_MEMORY_LIMIT_DEFAULT}"
        )
        return parse_size_to_kb(TOOL_MEMORY_LIMIT_DEFAULT)


def get_ulimit_prefix() -> str:
    """
    Get the ulimit command prefix for memory protection.

    Returns a shell command prefix that sets virtual memory limit.
    The '|| true' ensures we continue even if ulimit is not supported.
    """
    memory_limit_kb = get_memory_limit_kb()
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
        current_limit = os.environ.get(TOOL_MEMORY_LIMIT_ENV, TOOL_MEMORY_LIMIT_DEFAULT)
        hint = (
            f"\n\n[OOM] Command was killed due to memory limits (current limit: {current_limit}). "
            f"Try querying the data differently to reduce memory usage - add filters to narrow the results, "
            f"use smaller time ranges, or try alternative tools that may be more memory-efficient. "
            f"If you cannot succeed with a modified query, you may recommend the user increase the limit "
            f"by setting {TOOL_MEMORY_LIMIT_ENV} (e.g., '1GB', '2GB')."
        )
        return output + hint

    return output
