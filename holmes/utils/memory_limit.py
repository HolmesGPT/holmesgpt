"""
Memory limit utilities for tool subprocess execution.
"""

import logging
import os
import signal
import subprocess
import threading
import time
from collections import defaultdict
from typing import Optional, Tuple

from holmes.common.env_vars import (
    TOOL_MAX_OUTPUT_LENGTH,
    TOOL_MEMORY_LIMIT_MB,
    TOOL_VIRTUAL_MEMORY_HEADROOM_MB,
)

logger = logging.getLogger(__name__)

# Maximum number of lines to keep from OOM crash output.
# The first few lines contain the error message; the rest is typically
# goroutine stack dumps (Go) or core-dump noise that wastes tokens.
OOM_OUTPUT_MAX_LINES = 10

# Chunk size (in characters) used when draining a subprocess' stdout.
_READ_CHUNK_SIZE = 65536

# How often (seconds) the parent samples the subprocess tree's resident memory.
_RSS_POLL_INTERVAL_SECONDS = 0.05


def get_ulimit_prefix() -> str:
    """
    Get the ulimit command prefix for memory protection.

    Returns a shell command prefix that sets a *virtual* memory limit
    (RLIMIT_AS) on the subprocess as a cheap, kernel-enforced backstop.

    This is only a backstop: the primary, accurate enforcement of the resident
    memory budget (``TOOL_MEMORY_LIMIT_MB``) happens in the parent via RSS
    polling (see ``read_process_output_capped``). Because ``ulimit -v`` caps
    *virtual* address space — which Go tools like kubectl reserve far in excess
    of their resident usage — the cap is set to the resident budget plus a
    generous headroom so it does not spuriously kill legitimate tools, while
    still catching instantaneous virtual explosions the RSS poll might miss.

    The '|| true' ensures we continue even if ulimit is not supported.
    Returns an empty prefix (no backstop) when TOOL_VIRTUAL_MEMORY_HEADROOM_MB
    is 0 or negative — the resident-memory RSS poll is then the sole enforcer.
    """
    if TOOL_VIRTUAL_MEMORY_HEADROOM_MB <= 0:
        return ""
    virtual_limit_mb = TOOL_MEMORY_LIMIT_MB + TOOL_VIRTUAL_MEMORY_HEADROOM_MB
    memory_limit_kb = virtual_limit_mb * 1024
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
            f"[OOM] The command's result was too large: it exceeded the memory limit "
            f"({TOOL_MEMORY_LIMIT_MB} MB) while building its output. "
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


def _sample_tree_rss_mb(root_pid: int) -> Optional[float]:
    """Sum the resident memory (VmRSS) of a process and all its descendants.

    Reads ``/proc`` directly (no psutil dependency). Returns ``None`` when
    ``/proc`` is unavailable (e.g. macOS), signalling the caller to disable the
    RSS guard. Per-pid errors (a pid that exits mid-walk) are swallowed so a
    racing exit never crashes the sampler.
    """
    if not os.path.isdir("/proc"):
        return None

    # Build a parent -> children map from every process' PPID.
    children: dict = defaultdict(list)
    try:
        pids = [int(entry) for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                data = f.read()
            # comm (field 2) is wrapped in parens and may contain spaces/parens;
            # everything after the last ')' is fixed-width, so ppid is field 4.
            rest = data[data.rindex(b")") + 2 :].split()
            ppid = int(rest[1])
            children[ppid].append(pid)
        except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
            continue

    # BFS from the root pid, summing VmRSS.
    total_kb = 0
    seen: set = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, ()))
        try:
            with open(f"/proc/{pid}/status", "rb") as f:
                for line in f:
                    if line.startswith(b"VmRSS:"):
                        total_kb += int(line.split()[1])
                        break
        except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
            continue

    return total_kb / 1024.0


def _kill_process_group(process: "subprocess.Popen", pgid: Optional[int]) -> None:
    """Kill the whole process group, falling back to killing the direct child.

    ``pgid`` is the group id captured right after launch (before any reap could
    recycle the pid). Killing the group reaches grandchildren (e.g. kubectl
    spawned by the ``/bin/bash -c`` wrapper); a plain ``process.kill()`` would
    only kill the shell and orphan kubectl.
    """
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            pass
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass


def read_process_output_capped(
    process: "subprocess.Popen",
    timeout: Optional[float] = None,
    max_output_chars: Optional[int] = None,
    max_rss_mb: Optional[int] = None,
) -> Tuple[str, bool, bool, bool]:
    """Read a subprocess' stdout while bounding time, output size and memory.

    Three independent safeguards protect the Holmes container:

    - **Output cap** (``max_output_chars``): drains ``process.stdout`` in chunks
      and stops once the cap is reached, so the *parent* never buffers an
      unbounded amount of data.
    - **RSS budget** (``max_rss_mb``): polls the subprocess *tree's* resident
      memory via ``/proc`` and kills the process group when it exceeds the
      budget — the accurate, language-agnostic real-memory enforcer (the
      ``ulimit -v`` backstop only caps virtual memory).
    - **Timeout** (``timeout``): wall-clock limit.

    The process MUST be created with ``stdout=subprocess.PIPE``,
    ``stderr=subprocess.STDOUT`` in text mode, and ``start_new_session=True`` so
    the whole tree can be killed via its process group.

    Args:
        process: The already-started subprocess.
        timeout: Max seconds to wait, or ``None`` to wait indefinitely.
        max_output_chars: Max characters to keep. ``None`` uses
            ``TOOL_MAX_OUTPUT_LENGTH``; ``0`` disables the cap.
        max_rss_mb: Resident-memory budget for the process tree. ``None`` uses
            ``TOOL_MEMORY_LIMIT_MB``; ``0`` disables RSS polling.

    Returns:
        (output, timed_out, truncated, mem_killed)
    """
    if max_output_chars is None:
        max_output_chars = TOOL_MAX_OUTPUT_LENGTH
    if max_rss_mb is None:
        max_rss_mb = TOOL_MEMORY_LIMIT_MB

    # Capture the process group id now, while the process is guaranteed alive,
    # to avoid a pid-recycling race at kill time.
    pgid: Optional[int]
    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, OSError, AttributeError):
        pgid = None

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

    rss_enabled = bool(max_rss_mb) and os.path.isdir("/proc")
    deadline = None if timeout is None else time.monotonic() + timeout
    timed_out = False
    mem_killed = False

    # Poll loop: wake up frequently to check reader completion, wall-clock
    # timeout, and the subprocess tree's resident memory.
    while True:
        reader.join(_RSS_POLL_INTERVAL_SECONDS)
        if not reader.is_alive():
            break  # stdout closed (EOF or truncation break)
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            break
        if rss_enabled:
            rss = _sample_tree_rss_mb(process.pid)
            if rss is not None and rss > max_rss_mb:
                mem_killed = True
                logger.warning(
                    f"Tool subprocess tree exceeded resident memory budget "
                    f"({rss:.0f} MB > {max_rss_mb} MB); killing process group."
                )
                break

    # Kill BEFORE wait: we must kill the group while pgid is still valid and the
    # leader has not been reaped. Also kill when the reader broke on the output
    # cap (the child may be blocked writing to a full pipe).
    if timed_out or mem_killed or reader_state["truncated"]:
        _kill_process_group(process, pgid)

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("Subprocess did not exit within 5s after kill()")

    # Killing closes the pipe write ends, so the reader's blocked read() returns
    # the final buffered bytes then EOF. Join is the happens-before barrier that
    # guarantees the (partial) output is set before we read it.
    reader.join(5)

    return (
        reader_state["output"],
        timed_out,
        reader_state["truncated"],
        mem_killed,
    )
