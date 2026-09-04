import logging
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from holmes.plugins.toolsets.bash.common.sandbox import (
    build_sandbox_argv,
    should_sandbox,
)
from holmes.utils.memory_limit import check_oom_and_append_hint, get_ulimit_prefix

logger = logging.getLogger(__name__)


# Grace period (seconds) to let a timed-out argv child exit on SIGTERM — and run
# any cleanup it does on termination — before it is force-killed with SIGKILL.
ARGV_TERMINATE_GRACE_SECONDS = 5


@dataclass
class BashResult:
    """Simple result type for bash command execution."""

    stdout: str
    return_code: Optional[int]
    timed_out: bool


def execute_bash_command(
    cmd: str, timeout: int, sandbox_bind_paths: Optional[list] = None
) -> BashResult:
    """
    Execute a bash command and return the result.

    Args:
        cmd: The bash command to execute
        timeout: Timeout in seconds
        sandbox_bind_paths: Host directories to bind-mount read-write into the
            sandbox at the same absolute path (e.g. the per-session tool-result
            spill dir, so the LLM can cat/grep spilled results). Ignored when the
            sandbox is not active.

    Returns:
        BashResult with stdout, return_code, and timed_out flag
    """
    protected_cmd = get_ulimit_prefix() + cmd

    if should_sandbox():
        # Run inside a bubblewrap jail. The ulimit-prefixed command is handed to
        # bash -c *inside* the jail, so shell semantics are preserved while the
        # host environment, credentials, and filesystem are isolated away.
        logger.debug("Executing bash command inside bubblewrap sandbox")
        popen_args: list = build_sandbox_argv(
            protected_cmd, rw_bind_paths=sandbox_bind_paths
        )
        popen_kwargs: dict = {}
    else:
        popen_args = protected_cmd  # type: ignore[assignment]
        popen_kwargs = {"shell": True, "executable": "/bin/bash"}

    process = subprocess.Popen(
        popen_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **popen_kwargs,
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


def execute_argv_command(argv: List[str], timeout: int) -> BashResult:
    """
    Execute a command given as an argv list, WITHOUT a shell (shell=False).

    Unlike ``execute_bash_command``, no shell interprets the arguments, so shell
    metacharacters in ``argv`` (``;``, ``|``, ``&``, ``$(...)``, backticks, ...) are
    passed verbatim as literal argument bytes and can never spawn a host command.
    Use this whenever any element of ``argv`` is derived from untrusted input.

    Note: the ``ulimit -v`` memory prefix applied by ``execute_bash_command`` is a
    shell construct and therefore intentionally not applied here. Callers that run
    a thin client such as ``kubectl`` (where the heavy work happens elsewhere and
    the call is bounded by ``timeout``) do not need it.

    On timeout the child is first sent SIGTERM (and only SIGKILLed if it does not
    exit within a short grace period), so a client that cleans up on termination
    — e.g. ``kubectl run --rm``, which deletes its debug pod on a graceful exit —
    gets the chance to do so instead of being killed outright.

    Args:
        argv: The command and its arguments as a list (e.g. ["kubectl", "run", ...])
        timeout: Timeout in seconds

    Returns:
        BashResult with stdout, return_code, and timed_out flag
    """
    process = subprocess.Popen(
        argv,
        shell=False,
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
        # Graceful first: give the client a chance to run its own cleanup.
        process.terminate()
        try:
            stdout, _ = process.communicate(timeout=ARGV_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            # Client ignored SIGTERM — force it and reap.
            process.kill()
            stdout, _ = process.communicate()
        stdout = stdout.strip() if stdout else ""

        return BashResult(
            stdout=stdout,
            return_code=None,
            timed_out=True,
        )
