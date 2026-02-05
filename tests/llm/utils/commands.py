# type: ignore
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Dict, Optional

from tests.llm.utils.env_vars import is_run_live_enabled
from tests.llm.utils.test_case_utils import HolmesTestCase, generate_run_id

if TYPE_CHECKING:
    from tests.llm.utils.env_config import EnvConfig

EVAL_SETUP_TIMEOUT = int(
    os.environ.get("EVAL_SETUP_TIMEOUT", "300")
)  # Default timeout in seconds

# Module-level storage for run_ids to ensure they persist between setup and test execution
# Key: test_case.id, Value: generated run_id
_TEST_RUN_IDS: Dict[str, str] = {}


def _get_storage_id(test_case: HolmesTestCase) -> str:
    """Get the storage key for a test case's run_id.

    Uses base_id if present (for variant tests), otherwise test_case.id.
    This matches the deduplication logic in setup_cleanup.py.
    """
    return getattr(test_case, "base_id", None) or test_case.id


def get_test_run_id(test_case: HolmesTestCase) -> str:
    """Get the run_id for a test case.

    This retrieves the run_id that was generated during setup for reliable
    use in render_user_prompt. Using module-level storage because
    object.__setattr__ on Pydantic models is unreliable.

    For variant tests (tests with array user_prompt), all variants share
    the same run_id since setup only runs once per base test.
    """
    storage_id = _get_storage_id(test_case)
    return _TEST_RUN_IDS.get(storage_id, "")


def _get_pod_diagnostics(test_case: Optional[HolmesTestCase], operation: str) -> str:
    """Get pod and event diagnostics for debugging failures.

    Args:
        test_case: The test case object containing test metadata
        operation: The operation type ("setup" or other)

    Returns:
        A string with pod diagnostics or empty string if not applicable
    """
    if not test_case or not test_case.id or operation != "setup":
        return ""

    diagnostics = []

    try:
        # Extract just the numeric ID from the test case (e.g., "999" from "999_test_pod_diagnostics")
        test_id = test_case.id.split("_")[0] if "_" in test_case.id else test_case.id

        # Get pod status
        # grep -E uses extended regex to match either:
        # - ^NAMESPACE: lines starting with "NAMESPACE" (the header line)
        # - {test_id}: any line containing the test ID (e.g., "999" for app-999 namespace)
        diagnostic_cmd = f"kubectl get pods -A | grep -E '(^NAMESPACE|{test_id})'"
        pod_status_result = subprocess.run(
            diagnostic_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=test_case.folder,
        )
        if pod_status_result.stdout:
            diagnostics.append(
                f"\nPod status for debugging (command: {diagnostic_cmd}):\n{pod_status_result.stdout}"
            )
        else:
            diagnostics.append(
                f"\nPod status for debugging (command: {diagnostic_cmd}):\nNo matching pods found"
            )

        # Get namespace events to show scheduling issues, failures, etc.
        # This is particularly helpful for diagnosing resource constraints and scheduling problems
        # First, find the actual namespace(s) being used by looking for namespaces matching the test_id
        ns_cmd = f"kubectl get namespaces | grep -E '{test_id}' | awk '{{print $1}}'"
        ns_result = subprocess.run(
            ns_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=test_case.folder,
        )

        namespaces_found = (
            ns_result.stdout.strip().split("\n") if ns_result.stdout.strip() else []
        )

        if namespaces_found:
            for namespace in namespaces_found:
                if namespace:  # Skip empty strings
                    events_cmd = f"kubectl get events -n {namespace} --sort-by='.lastTimestamp' | tail -20"
                    events_result = subprocess.run(
                        events_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        cwd=test_case.folder,
                    )
                    if events_result.stdout:
                        diagnostics.append(
                            f"\nRecent events in namespace {namespace} (command: {events_cmd}):\n{events_result.stdout}"
                        )
        else:
            # Fall back to default namespace pattern if no matching namespaces found
            namespace = f"app-{test_id}" if test_id.isdigit() else f"test-{test_id}"
            diagnostics.append(
                f"\nNo namespaces found matching pattern '{test_id}' (tried default: {namespace})"
            )

        return "\n".join(diagnostics)

    except Exception as e:
        return f"\n\nFailed to get diagnostics: {str(e)}"


def _truncate_script(script: str, max_lines: int = 10) -> str:
    """Truncate long scripts for display in error messages."""
    lines = script.strip().split("\n")
    if len(lines) <= max_lines:
        return script

    # Show first 5 and last 3 lines
    truncated = (
        lines[:5]
        + ["... (truncated - " + str(len(lines) - 8) + " lines) ..."]
        + lines[-3:]
    )
    return "\n".join(truncated)


class CommandResult:
    def __init__(
        self,
        command: str,
        test_case_id: str,
        success: bool,
        exit_code: int = None,
        elapsed_time: float = 0,
        error_type: str = None,
        error_details: str = None,
    ):
        self.command = command
        self.test_case_id = test_case_id
        self.success = success
        self.exit_code = exit_code
        self.elapsed_time = elapsed_time
        self.error_type = error_type  # 'timeout', 'failure', or None
        self.error_details = error_details

    @property
    def exit_info(self) -> str:
        """Get formatted exit information."""
        return (
            f"exit {self.exit_code}" if self.exit_code is not None else "no exit code"
        )


def _invoke_command(
    command: str,
    cwd: str,
    timeout: Optional[int] = None,
    suppress_logging: bool = False,
    extra_env: Optional[Dict[str, str]] = None,
) -> str:
    try:
        actual_timeout = timeout if timeout is not None else EVAL_SETUP_TIMEOUT
        logging.debug(f"Running `{command}` in {cwd} with timeout {actual_timeout}s")

        # Merge extra env vars with current environment
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        result = subprocess.run(
            command,
            shell=True,
            executable="/bin/bash",  # Force bash instead of default /bin/sh
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            timeout=actual_timeout,
            env=env,
        )

        output = f"{result.stdout}\n{result.stderr}"
        logging.debug(f"** `{command}`:\n{output}")
        logging.debug(f"Ran `{command}` in {cwd} with exit code {result.returncode}")

        # Show output if SHOW_SETUP_OUTPUT is set
        if os.environ.get("SHOW_SETUP_OUTPUT", "").lower() in ("true", "1"):
            if result.stdout:
                sys.stderr.write(f"[SETUP OUTPUT] {result.stdout}\n")
            if result.stderr:
                sys.stderr.write(f"[SETUP STDERR] {result.stderr}\n")

        return output
    except subprocess.TimeoutExpired as e:
        # For timeout, we need to manually capture any partial output
        # Note: subprocess.run with timeout doesn't capture partial output by default
        # We'll add the captured output to the exception before re-raising
        e.stdout = getattr(e, "stdout", "")
        e.stderr = getattr(e, "stderr", "")
        if not suppress_logging:
            truncated_command = _truncate_script(command)
            message = f"Command `{truncated_command}` timed out after {actual_timeout}s\nPartial stdout:\n{e.stdout}\nPartial stderr:\n{e.stderr}"
            logging.error(message)
        raise e
    except subprocess.CalledProcessError as e:
        if not suppress_logging:
            truncated_command = _truncate_script(command)
            message = f"Command `{truncated_command}` failed with return code {e.returncode}\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}"
            logging.error(message)
        raise e


def run_commands(
    test_case: HolmesTestCase, commands_str: str, operation: str
) -> CommandResult:
    """Generic command runner for setup/cleanup operations.

    For setup operations, generates a unique EVAL_RUN_ID that is:
    - Available as env var in the before_test script
    - Stored in module-level dict for use in user_prompt templating
    - Used to prevent tests from succeeding on cached data

    Note: EVAL_RUN_ID is always generated for setup operations, even when
    --skip-setup is used or no before_test script exists. This ensures
    tests that use {{ env.EVAL_RUN_ID }} in their prompts still work.
    """
    # Generate and store run_id for setup operations if not already set
    # (generate_all_run_ids may have already set it, e.g., for --skip-setup support)
    storage_id = _get_storage_id(test_case)
    if operation == "setup" and storage_id not in _TEST_RUN_IDS:
        run_id = generate_run_id()
        _TEST_RUN_IDS[storage_id] = run_id
        logging.debug(f"Generated EVAL_RUN_ID={run_id} for test {test_case.id} (storage_id={storage_id})")

    if not commands_str or not is_run_live_enabled():
        return CommandResult(
            command=f"(no {operation} needed)",
            test_case_id=test_case.id,
            success=True,
            elapsed_time=0,
        )

    start_time = time.time()
    # Execute the entire script as a single command instead of splitting by lines
    # This preserves multi-line bash constructs like if/then/else, for loops, etc.
    script = commands_str.strip()

    # Set up environment variables for the script
    extra_env: Dict[str, str] = {}
    if operation == "setup":
        # Use the run_id we already generated and stored above
        extra_env["EVAL_RUN_ID"] = _TEST_RUN_IDS[storage_id]
    elif operation == "cleanup":
        # Reuse the same run_id for cleanup
        run_id = _TEST_RUN_IDS.get(storage_id, "")
        if run_id:
            extra_env["EVAL_RUN_ID"] = run_id

    try:
        # Execute the entire commands string as a single bash script
        # Use per-test timeout if specified, otherwise use default
        timeout = (
            test_case.setup_timeout
            if hasattr(test_case, "setup_timeout") and test_case.setup_timeout
            else None
        )
        _invoke_command(
            command=script,
            cwd=test_case.folder,
            timeout=timeout,
            suppress_logging=True,
            extra_env=extra_env,
        )

        elapsed_time = time.time() - start_time
        return CommandResult(
            command=f"{operation.capitalize()}: completed",
            test_case_id=test_case.id,
            success=True,
            elapsed_time=elapsed_time,
        )
    except subprocess.CalledProcessError as e:
        elapsed_time = time.time() - start_time

        # Always add pod diagnostics for any failure during setup
        extra_diagnostics = _get_pod_diagnostics(test_case, operation)

        # Don't log here - setup_cleanup.py will handle all logging consistently
        # to avoid duplicate diagnostic output

        error_details = f"Exit code: {e.returncode}\n\nstderr:\n{e.stderr}\n\nstdout:\n{e.stdout}{extra_diagnostics}"

        return CommandResult(
            command=f"{operation.capitalize()} failed at: {e.cmd}",
            test_case_id=test_case.id,
            success=False,
            exit_code=e.returncode,
            elapsed_time=elapsed_time,
            error_type="failure",
            error_details=error_details,
        )
    except subprocess.TimeoutExpired as e:
        elapsed_time = time.time() - start_time

        # Try to capture partial output from the timed-out process
        partial_stdout = ""
        partial_stderr = ""
        if hasattr(e, "stdout") and e.stdout:
            partial_stdout = e.stdout
        if hasattr(e, "stderr") and e.stderr:
            partial_stderr = e.stderr

        # Add pod diagnostics for timeout errors too
        extra_diagnostics = _get_pod_diagnostics(test_case, operation)

        # Include partial output if available
        output_section = ""
        if partial_stdout or partial_stderr:
            output_section = f"\n\nPartial output before timeout:\nstdout:\n{partial_stdout}\n\nstderr:\n{partial_stderr}"

        error_details = f"TIMEOUT after {e.timeout}s (default: {EVAL_SETUP_TIMEOUT}s)\n\nYou can increase timeout with environment variable EVAL_SETUP_TIMEOUT=<seconds> or by setting 'setup_timeout' in test_case.yaml{output_section}{extra_diagnostics}"

        return CommandResult(
            command=f"{operation.capitalize()} timeout: {e.cmd}",
            test_case_id=test_case.id,
            success=False,
            elapsed_time=elapsed_time,
            error_type="timeout",
            error_details=error_details,
        )
    except Exception as e:
        elapsed_time = time.time() - start_time
        error_details = f"Unexpected error: {str(e)}"

        return CommandResult(
            command=f"{operation.capitalize()} failed",
            test_case_id=test_case.id,
            success=False,
            elapsed_time=elapsed_time,
            error_type="failure",
            error_details=error_details,
        )


@contextmanager
def _temporary_env_vars(env_vars: Dict[str, str]):
    """Context manager that temporarily sets environment variables and restores them on exit.

    Values support environment variable expansion via os.path.expandvars.
    """
    if not env_vars:
        yield
        return

    saved: Dict[str, Optional[str]] = {key: os.environ.get(key) for key in env_vars}

    try:
        for key, value in env_vars.items():
            os.environ[key] = os.path.expandvars(value)
        yield
    finally:
        for key, original in saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


@contextmanager
def set_test_env_vars(test_case: HolmesTestCase):
    """Context manager to set test case environment variables during execution.

    Also sets EVAL_RUN_ID from module-level storage if available, making the unique
    run identifier available during test execution (for toolset configs, etc.).
    """
    # Build env vars to set, including EVAL_RUN_ID if available
    env_vars_to_set: Dict[str, str] = {}
    if test_case.test_env_vars:
        env_vars_to_set.update(test_case.test_env_vars)

    # Set EVAL_RUN_ID from module-level storage (test_case.run_id is never populated)
    run_id = get_test_run_id(test_case)
    if run_id:
        env_vars_to_set["EVAL_RUN_ID"] = run_id

    with _temporary_env_vars(env_vars_to_set):
        yield


@contextmanager
def apply_env_config(env_config: "EnvConfig"):
    """Context manager to apply an EnvConfig's environment variables during execution."""
    with _temporary_env_vars(env_config.env_vars):
        yield
