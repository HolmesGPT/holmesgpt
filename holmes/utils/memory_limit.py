"""
Memory limit utilities for tool subprocess execution.
"""

import logging
import os
import selectors
import signal
import subprocess
import time
from typing import List, Optional

from holmes.common.env_vars import TOOL_MEMORY_LIMIT_MB

logger = logging.getLogger(__name__)

# Maximum number of lines to keep from OOM crash output.
# The first few lines contain the error message; the rest is typically
# goroutine stack dumps (Go) or core-dump noise that wastes tokens.
OOM_OUTPUT_MAX_LINES = 10

TOOL_OUTPUT_BUFFER_LIMIT_CHARS = 50 * 1024 * 1024
READ_CHUNK_SIZE_CHARS = 64 * 1024
POST_KILL_DRAIN_SECONDS = 5


def get_ulimit_prefix() -> str:
    """
    Get the ulimit command prefix for memory protection.

    Returns a shell command prefix that sets virtual memory limit.
    The '|| true' ensures we continue even if ulimit is not supported.
    """
    memory_limit_kb = TOOL_MEMORY_LIMIT_MB * 1024
    # oom_score_adj=1000 marks the tool subprocess (and every child it spawns,
    # which inherit the value) as the kernel OOM killer's preferred victim, so
    # when the pod runs out of memory the kernel kills the tool subprocess tree
    # instead of the Holmes process itself. Raising one's own oom_score_adj
    # needs no privileges, and on systems without /proc (e.g. macOS) the
    # redirect+`|| true` make it a silent no-op.
    return (
        "echo 1000 > /proc/self/oom_score_adj 2>/dev/null || true; "
        f"ulimit -v {memory_limit_kb} 2>/dev/null || true; "
    )


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
            f"\n"
            f"For guidance on when and how to raise the limit, see:\n"
            f"https://holmesgpt.dev/data-sources/tool-execution-safety/#when-to-raise-the-limit\n"
            f"\n"
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


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, AttributeError):
        try:
            process.kill()
        except OSError:
            pass


# Like process.communicate(), but kills the subprocess once its output exceeds TOOL_OUTPUT_BUFFER_LIMIT_CHARS.
def communicate_capped(process: subprocess.Popen, timeout: Optional[float]) -> str:
    if process.stdout is None:
        process.wait(timeout=timeout)
        return ""
    fd = process.stdout.fileno()
    deadline = None if timeout is None else time.monotonic() + timeout
    chunks: List[bytes] = []
    total = 0
    timed_out = False
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                if timed_out:
                    break
                timed_out = True
                _kill_process_group(process)
                # Collect the residue the killed process left in the pipe, briefly
                deadline = now + POST_KILL_DRAIN_SECONDS
                continue
            wait_time = None if deadline is None else deadline - now
            if not selector.select(wait_time):
                continue
            try:
                chunk = os.read(fd, READ_CHUNK_SIZE_CHARS)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > TOOL_OUTPUT_BUFFER_LIMIT_CHARS:
                _kill_process_group(process)
                break
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Tool subprocess did not exit within 5s after kill()")
    output = b"".join(chunks).decode("utf-8", errors="replace")
    if timed_out:
        raise subprocess.TimeoutExpired(process.args, timeout or 0, output=output)
    return output
