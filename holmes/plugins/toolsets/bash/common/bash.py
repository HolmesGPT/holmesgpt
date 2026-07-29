import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from holmes.utils.memory_limit import (
    _kill_process_group,
    check_oom_and_append_hint,
    get_ulimit_prefix,
    read_capped_output,
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
    # Output goes to an unlinked temp file instead of a pipe: the kernel enforces
    # the size cap via the `ulimit -f` prefix, and a file cannot deadlock the child
    # or require the parent to buffer anything while the command runs.
    with tempfile.TemporaryFile(
        mode="w+", encoding="utf-8", errors="replace"
    ) as stdout_file:
        process = subprocess.Popen(
            protected_cmd,
            shell=True,
            executable="/bin/bash",
            stdout=stdout_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )

        try:
            process.wait(timeout=timeout)
            stdout = read_capped_output(stdout_file)
            stdout = stdout.strip() if stdout else ""
            stdout = check_oom_and_append_hint(stdout, process.returncode)

            return BashResult(
                stdout=stdout,
                return_code=process.returncode,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # Collect any partial output that was generated before timeout
            stdout = read_capped_output(stdout_file)
            stdout = stdout.strip() if stdout else ""

            return BashResult(
                stdout=stdout,
                return_code=None,
                timed_out=True,
            )
