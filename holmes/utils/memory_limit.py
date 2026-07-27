"""
Memory limit utilities for tool subprocess execution.
"""

import logging
import subprocess
import threading
from typing import Optional, Tuple

from holmes.common.env_vars import TOOL_MAX_OUTPUT_LENGTH, TOOL_MEMORY_LIMIT_MB

logger = logging.getLogger(__name__)

# Maximum number of lines to keep from OOM crash output.
# The first few lines contain the error message; the rest is typically
# goroutine stack dumps (Go) or core-dump noise that wastes tokens.
OOM_OUTPUT_MAX_LINES = 10

# Chunk size (in characters) used when draining a subprocess' stdout.
_READ_CHUNK_SIZE = 65536


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


def append_output_truncated_hint(output: str) -> str:
    """Prepend a hint explaining that the command output was truncated.

    Unlike an OOM (where the command crashed and produced little useful data),
    a length-based truncation means the command succeeded but produced more
    output than Holmes will hold in memory. The kept prefix is still useful, so
    we prepend the hint and keep the (already capped) output.
    """
    hint = (
        f"[OUTPUT TRUNCATED] The command produced more than the maximum "
        f"{TOOL_MAX_OUTPUT_LENGTH} characters Holmes will read into memory, so the "
        f"output below was cut off. This is a safety limit to avoid exhausting the "
        f"Holmes container's memory — it is NOT an error or bug.\n"
        f"\n"
        f"Note to agent: The result is incomplete. Retry with a narrower query so the "
        f"full output fits — for example filter by namespace, label selector, or a "
        f"specific resource name; avoid broad `-o yaml`/`-o json` dumps across many "
        f"objects (prefer `-o custom-columns`, `-o jsonpath`, or `kubectl get` without "
        f"`-o yaml`). If you genuinely need the full output, the user can raise the "
        f"TOOL_MAX_OUTPUT_LENGTH environment variable."
    )
    if output:
        return hint + "\n\n" + output
    return hint


def read_process_output_capped(
    process: "subprocess.Popen",
    timeout: Optional[float] = None,
    max_output_chars: Optional[int] = None,
) -> Tuple[str, bool, bool]:
    """Read a subprocess' stdout while bounding both time and memory.

    The `ulimit -v` prefix bounds the *child* process, but the parent still has
    to hold whatever the child writes to stdout. This drains ``process.stdout``
    in chunks and stops once ``max_output_chars`` characters have been collected,
    discarding the rest so the parent never buffers an unbounded amount of data.

    The process must be created with ``stdout=subprocess.PIPE`` and
    ``stderr=subprocess.STDOUT`` in text mode.

    Args:
        process: The already-started subprocess.
        timeout: Max seconds to wait for the process to finish. ``None`` waits
            indefinitely.
        max_output_chars: Max characters to keep. ``None`` uses
            ``TOOL_MAX_OUTPUT_LENGTH``; ``0`` disables the cap.

    Returns:
        (output, timed_out, truncated)
    """
    if max_output_chars is None:
        max_output_chars = TOOL_MAX_OUTPUT_LENGTH

    reader_state: dict = {"output": "", "truncated": False}

    def _reader() -> None:
        assert process.stdout is not None
        chunks = []
        total = 0
        truncated = False
        while True:
            chunk = process.stdout.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            if max_output_chars and total + len(chunk) >= max_output_chars:
                chunks.append(chunk[: max_output_chars - total])
                truncated = True
                # Stop reading. The child may block writing to the now-full pipe;
                # the caller kills it below to unblock and reap it.
                break
            chunks.append(chunk)
            total += len(chunk)
        reader_state["output"] = "".join(chunks)
        reader_state["truncated"] = truncated

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    reader.join(timeout)

    timed_out = reader.is_alive()
    truncated = reader_state["truncated"]

    if timed_out or truncated:
        # On timeout the reader is still blocked on read(); on truncation the
        # child may be blocked writing to a full pipe. Killing closes the pipe,
        # which unblocks and lets the reader thread finish.
        process.kill()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Subprocess did not exit within 5s after kill()")

    # Give the reader thread a moment to drain any final bytes and set output.
    reader.join(1)

    return reader_state["output"], timed_out, reader_state["truncated"]
