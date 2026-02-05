"""Setup and cleanup infrastructure for test cases."""

import logging
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from strenum import StrEnum

from tests.llm.utils.commands import run_commands  # type: ignore[attr-defined]
from tests.llm.utils.test_case_utils import HolmesTestCase  # type: ignore[attr-defined]

# Configuration
# Use as many workers as there are setups to run them all concurrently
MAX_WORKERS = 999


def log(msg, error=False, dark_red=False):
    """Force a log to be written even with xdist, which captures stdout. (must use -s to see this)

    Args:
        msg: The message to log
        error: If True, log as error (red color) instead of info
        dark_red: If True, use dark red instead of bright red (overrides error)
    """
    if os.environ.get("PYTEST_XDIST_WORKER"):
        # If running under xdist, we log to stderr so it appears in the pytest output
        # This is necessary because xdist captures stdout and doesn't show it in the output
        if sys.stderr.isatty():
            if dark_red:
                # Use standard red (darker) for strict setup mode errors
                sys.stderr.write(f"\033[31m{msg}\033[0m")
            elif error:
                # Use bright red for regular errors
                sys.stderr.write(f"\033[91m{msg}\033[0m")
            else:
                sys.stderr.write(msg)
        else:
            sys.stderr.write(msg)
        sys.stderr.write("\n")
    else:
        # If not running under xdist, use appropriate log level
        if error or dark_red:
            logging.error(msg)
        else:
            logging.info(msg)


class Operation(StrEnum):
    """Enum for operation types."""

    SETUP = "Setup"
    CLEANUP = "Cleanup"


def run_all_test_commands(
    test_cases: List[HolmesTestCase], operation: Operation
) -> Dict[str, str]:
    """Run before_test/after_test (according to operation)

    Args:
        test_cases: List of test cases to process
        operation: Operation enum indicating SETUP or CLEANUP

    Returns:
        Dict[str, str]: Mapping of test_case.id to error message for failed setups
    """
    operation_lower = operation.value.lower()
    operation_plural = f"{operation_lower}s"

    log(
        f"\n⚙️ {'Running before_test' if operation == Operation.SETUP else 'Running after_test'} for {len(test_cases)} unique evals: {', '.join(tc.id for tc in test_cases)}"
    )

    start_time = time.time()
    successful_test_cases = 0
    failed_test_cases = 0
    timed_out_test_cases = 0
    failed_setup_info = {}  # Map test_case.id to error message

    # Track which tests are still pending
    pending_test_ids = {tc.id for tc in test_cases}
    completed_test_ids = set()

    with ThreadPoolExecutor(max_workers=min(len(test_cases), MAX_WORKERS)) as executor:
        if operation == Operation.SETUP:
            future_to_test_case = {
                executor.submit(
                    run_commands, test_case, test_case.before_test, operation_lower
                ): test_case
                for test_case in test_cases
            }
        else:
            future_to_test_case = {
                executor.submit(
                    run_commands, test_case, test_case.after_test, operation_lower
                ): test_case
                for test_case in test_cases
            }

        # Wait for all tasks to complete and handle results
        for future in as_completed(future_to_test_case):
            test_case = future_to_test_case[future]
            try:
                result = future.result()  # Single CommandResult for the test case

                # Update pending/completed sets
                completed_test_ids.add(test_case.id)
                pending_test_ids.discard(test_case.id)

                # Build remaining info string
                remaining_count = len(pending_test_ids)
                if remaining_count > 0:
                    # Show up to 5 remaining test IDs (only first part before underscore)
                    remaining_sample = list(sorted(pending_test_ids))[:5]
                    # Extract only the first part (e.g., "01" from "01_how_many_pods")
                    remaining_sample_short = [
                        tid.split("_")[0] for tid in remaining_sample
                    ]
                    if remaining_count > 5:
                        remaining_info = f"{remaining_count} ({', '.join(remaining_sample_short)}...)"
                    else:
                        remaining_info = (
                            f"{remaining_count} ({', '.join(remaining_sample_short)})"
                        )
                else:
                    remaining_info = "0"

                if result.success:
                    successful_test_cases += 1
                    log(
                        f"✅ {operation.value} {test_case.id}: {result.command} ({result.elapsed_time:.2f}s); {operation_plural} remaining: {remaining_info}"
                    )
                elif result.error_type == "timeout":
                    timed_out_test_cases += 1
                    log(
                        f"\n⏰ {operation.value} {test_case.id}: TIMEOUT after {result.elapsed_time:.2f}s; {operation_plural} remaining: {remaining_info}"
                    )

                    # Display error details including pod diagnostics
                    log(f"\n{result.error_details}\n", error=True)

                    # Store failure info for setup with detailed information
                    if operation == Operation.SETUP:
                        # Store the full error details without truncation for Braintrust
                        failed_setup_info[test_case.id] = result.error_details

                    # Emit warning to make it visible in pytest output
                    warnings.warn(
                        f"{operation.value} timeout for test {test_case.id}: Command '{result.command}' timed out after {result.elapsed_time:.2f}s. Output: {result.error_details}",
                        UserWarning,
                        stacklevel=2,
                    )
                else:
                    failed_test_cases += 1
                    log(
                        f"❌ {operation.value} {test_case.id}: FAILED ({result.exit_info}, {result.elapsed_time:.2f}s); {operation_plural} remaining: {remaining_info}"
                    )

                    # Display error details including pod diagnostics
                    log(f"\n{result.error_details}\n", error=True)

                    # Store failure info for setup with detailed information
                    if operation == Operation.SETUP:
                        # Store the full error details without truncation for Braintrust
                        failed_setup_info[test_case.id] = result.error_details

                    # Emit warning to make it visible in pytest output
                    warnings.warn(
                        f"{operation.value} failed for test {test_case.id}: Command '{result.command}' failed with {result.exit_info} in {result.elapsed_time:.2f}s. Output: {result.error_details}",
                        UserWarning,
                        stacklevel=2,
                    )

            except Exception as e:
                failed_test_cases += 1

                # Update pending/completed sets
                completed_test_ids.add(test_case.id)
                pending_test_ids.discard(test_case.id)

                # Build remaining info string
                remaining_count = len(pending_test_ids)
                if remaining_count > 0:
                    # Show up to 5 remaining test IDs (only first part before underscore)
                    remaining_sample = list(sorted(pending_test_ids))[:5]
                    # Extract only the first part (e.g., "01" from "01_how_many_pods")
                    remaining_sample_short = [
                        tid.split("_")[0] for tid in remaining_sample
                    ]
                    if remaining_count > 5:
                        remaining_info = f"{remaining_count} ({', '.join(remaining_sample_short)}...)"
                    else:
                        remaining_info = (
                            f"{remaining_count} ({', '.join(remaining_sample_short)})"
                        )
                else:
                    remaining_info = "0"

                log(
                    f"❌ {operation.value} {test_case.id}: EXCEPTION; {operation_plural} remaining: {remaining_info}"
                )
                log(f"\n{str(e)}", error=True)

                # Store failure info for setup
                if operation == Operation.SETUP:
                    failed_setup_info[test_case.id] = f"Setup exception: {str(e)}"

                # Emit warning to make it visible in pytest output
                warnings.warn(
                    f"{operation.value} exception for test {test_case.id}: {str(e)}",
                    UserWarning,
                    stacklevel=2,
                )

    elapsed_time = time.time() - start_time
    log(
        f"⚙️ {operation.value} completed in {elapsed_time:.2f}s: {successful_test_cases} successful, {failed_test_cases} failed, {timed_out_test_cases} timeout"
    )

    return failed_setup_info


def run_all_test_setup(
    test_cases: List[HolmesTestCase],
) -> Dict[str, str]:
    """Run before_test for each test case in parallel.

    Returns:
        Dict[str, str]: Mapping of test_case.id to error message for failed setups
    """
    # Run the before_test commands (which create namespaces, deployments, etc.)
    setup_failures = run_all_test_commands(test_cases, Operation.SETUP)

    return setup_failures


def run_all_test_cleanup(
    test_cases: List[HolmesTestCase],
) -> None:
    """Run after_test for each test case in parallel."""
    # Run the after_test commands
    run_all_test_commands(test_cases, Operation.CLEANUP)


def extract_llm_test_cases(session) -> List[HolmesTestCase]:
    """Extract unique LLM test cases from session items.

    Args:
        session: pytest session object

    Returns:
        List of unique HolmesTestCase instances (excluding skipped tests)
    """
    seen_ids = set()
    test_cases = []

    for item in session.items:
        if (
            item.get_closest_marker("llm")
            and hasattr(item, "callspec")
            and "test_case" in item.callspec.params
        ):
            test_case = item.callspec.params["test_case"]
            # Use base_id if present (for parameterized tests), otherwise use id
            dedup_id = getattr(test_case, "base_id", None) or test_case.id
            if (
                isinstance(test_case, HolmesTestCase)
                and dedup_id not in seen_ids
                and not test_case.skip  # Don't include skipped tests
            ):
                test_cases.append(test_case)
                seen_ids.add(dedup_id)

    return test_cases
