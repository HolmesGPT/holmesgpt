"""Run a shell command in its own process group so a timeout, or an interrupt,
can kill everything it started. With shell=True, killing only bash would leave
its children (kubectl, pipes) running and holding the output pipe."""

import os
import signal
import subprocess
import threading
from typing import Any, Collection, Dict, Optional, Tuple

TERMINATE_GRACE_SECONDS = 5

# Running commands, mapped to the ident of the thread that started them.
_running: Dict[subprocess.Popen, int] = {}
_running_lock = threading.Lock()


def run_shell_in_process_group(cmd: str, timeout: int) -> Tuple[str, Optional[int]]:
    """Run ``cmd`` with /bin/bash. Returns (output, return_code); return_code is
    None when the command timed out and its process group was killed."""
    process = subprocess.Popen(
        cmd,
        shell=True,
        executable="/bin/bash",
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    with _running_lock:
        _running[process] = threading.get_ident()
    try:
        stdout, _ = process.communicate(timeout=timeout)
        return stdout or "", process.returncode
    except subprocess.TimeoutExpired:
        return terminate_process_group(process), None
    except BaseException:
        terminate_process_group(process)
        raise
    finally:
        with _running_lock:
            _running.pop(process, None)


def terminate_running_commands(
    thread_ids: Optional[Collection[int]] = None,
) -> None:
    """Kill commands started by run_shell_in_process_group that are still
    running: all of them, or only those started by ``thread_ids``. Commands sit
    outside the terminal's foreground group, so Ctrl+C does not reach them;
    callers that are interrupted call this."""
    with _running_lock:
        processes = [
            process
            for process, owner in _running.items()
            if thread_ids is None or owner in thread_ids
        ]
    for process in processes:
        _signal_process_group(process, signal.SIGKILL)


def terminate_process_group(process: subprocess.Popen) -> str:
    """SIGTERM the group, SIGKILL it after a grace period, and return the
    output produced so far."""
    _signal_process_group(process, signal.SIGTERM)
    try:
        stdout, _ = process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        return stdout or ""
    except subprocess.TimeoutExpired:
        pass
    _signal_process_group(process, signal.SIGKILL)
    try:
        stdout, _ = process.communicate(timeout=TERMINATE_GRACE_SECONDS)
        return stdout or ""
    except subprocess.TimeoutExpired as e:
        # A grandchild that left the group (setsid) still holds the pipe;
        # stop reading rather than wait on it.
        if process.stdout:
            process.stdout.close()
        process.wait()
        return _decode_partial(e.output)


def _signal_process_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _decode_partial(output: Any) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""
