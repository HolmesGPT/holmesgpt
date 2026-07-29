import subprocess
from dataclasses import dataclass
from typing import Optional

from holmes.utils.memory_limit import (
    append_output_truncated_hint,
    check_oom_and_append_hint,
    get_ulimit_prefix,
    read_process_output_capped,
)


@dataclass
class BashResult:
    """Simple result type for bash command execution."""

    stdout: str
    return_code: Optional[int]
    timed_out: bool


def execute_bash_command(cmd: str, timeout: int) -> BashResult:
    """
    Execute a bash command and return the result.

    Args:
        cmd: The bash command to execute
        timeout: Timeout in seconds

    Returns:
        BashResult with stdout, return_code, and timed_out flag
    """
    protected_cmd = get_ulimit_prefix() + cmd
    process = subprocess.Popen(
        protected_cmd,
        shell=True,
        executable="/bin/bash",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # New session/process group so the whole tree (kubectl, not just the
        # /bin/bash wrapper) can be killed on timeout / memory breach.
        start_new_session=True,
    )

    stdout, timed_out, truncated, mem_killed = read_process_output_capped(
        process, timeout=timeout
    )
    stdout = stdout.strip() if stdout else ""

    if timed_out:
        return BashResult(
            stdout=stdout,
            return_code=None,
            timed_out=True,
        )

    if truncated:
        # The command succeeded but produced more output than we will buffer.
        # Keep the (capped) prefix and flag it so the LLM narrows its query.
        stdout = append_output_truncated_hint(stdout)
    elif mem_killed:
        # We killed the tree for exceeding the resident-memory budget. Surface
        # the OOM hint explicitly rather than relying on the exit code.
        stdout = check_oom_and_append_hint(stdout, 137)
    else:
        stdout = check_oom_and_append_hint(stdout, process.returncode)

    return BashResult(
        stdout=stdout,
        return_code=process.returncode,
        timed_out=False,
    )
