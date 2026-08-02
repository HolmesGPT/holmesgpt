import subprocess
from dataclasses import dataclass
from typing import Optional

from holmes.utils.memory_limit import check_oom_and_append_hint, get_ulimit_prefix


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
    )

    try:
        stdout, _ = process.communicate(timeout=timeout)
        stdout = stdout.strip() if stdout else ""
        stdout = check_oom_and_append_hint(stdout, process.returncode)

        return BashResult(
            stdout=stdout,
            return_code=process.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        process.kill()
        # Collect any partial output that was generated before timeout
        stdout, _ = process.communicate()
        stdout = stdout.strip() if stdout else ""

        return BashResult(
            stdout=stdout,
            return_code=None,
            timed_out=True,
        )
