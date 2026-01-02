"""Retry handler for dealing with API throttling and overload errors in LLM tests."""

import logging
import sys
import time
import warnings
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

# Throttling/overload error indicators
THROTTLE_ERROR_MESSAGES = [
    "overloaded_error",
    "Overloaded",
    "rate_limit_error",
    "RateLimitError",
    "throttled",
    "Too Many Requests",
    "429",
]

THROTTLE_ERROR_TYPES = [
    "InternalServerError",
    "RateLimitError",
    "ServiceUnavailableError",
    "OverloadedError",
]


class ThrottledError(Exception):
    """Custom exception to indicate the test was throttled after max retries."""

    pass


def is_throttle_error(error: Exception) -> bool:
    """Check if an error is a throttling/overload error that should be retried."""
    error_str = str(error)
    error_type = type(error).__name__

    # Check error message for throttle indicators
    for indicator in THROTTLE_ERROR_MESSAGES:
        if indicator in error_str:
            return True

    # Check error type
    for throttle_type in THROTTLE_ERROR_TYPES:
        if throttle_type in error_type:
            return True

    # Check for specific litellm exceptions
    if hasattr(error, "__class__"):
        class_name = error.__class__.__name__
        if "RateLimit" in class_name or "InternalServer" in class_name:
            return True

    return False


def retry_on_throttle(
    func: Callable[..., T],
    *args,
    retry_enabled: bool = True,
    test_id: str = "",
    model: str = "",
    request: Optional[Any] = None,  # pytest request object for setting properties
    **kwargs,
) -> T:
    """
    Retry a function when encountering throttling/overload errors.

    Args:
        func: The function to call
        *args: Positional arguments for the function
        retry_enabled: Whether retrying is enabled
        test_id: Test ID for logging
        model: Model name for logging
        **kwargs: Keyword arguments for the function

    Returns:
        The function result if successful

    Raises:
        ThrottledError: If all retries are exhausted
        Exception: If a non-throttle error occurs
    """
    # Retry delays in seconds: 60s, 300s (5m), 900s (15m)
    retry_delays = [60, 300, 900]
    attempt = 0
    last_error = None
    total_retry_time = 0

    while attempt <= len(retry_delays):
        try:
            # Try to execute the function
            # Add request back to kwargs if it exists (it was extracted for our use but func may need it)
            if request is not None:
                kwargs["request"] = request
            result = func(*args, **kwargs)

            # If we had to retry, issue a pytest warning and store retry info
            if attempt > 0 and request:
                retry_msg = (
                    f"Test {test_id} with model {model} succeeded after {attempt} retry attempt(s) "
                    f"(total retry wait time: {total_retry_time}s)"
                )
                warnings.warn(retry_msg, UserWarning, stacklevel=2)
                request.node.user_properties.append(("had_retries", True))
                request.node.user_properties.append(("retry_count", attempt))
                request.node.user_properties.append(
                    ("retry_wait_time", total_retry_time)
                )

            return result
        except Exception as e:
            # Check if this is a throttle error
            if not is_throttle_error(e):
                # Not a throttle error, re-raise immediately
                raise

            last_error = e

            # Mark that we encountered throttling (even if we might succeed later)
            if request and attempt == 0:  # Only mark once on first throttle
                request.node.user_properties.append(("encountered_throttling", True))

            # Check if retries are disabled
            if not retry_enabled:
                warning = (
                    f"\n⚠️  WARNING: Test {test_id} with model {model} encountered throttling/overload error:\n"
                    f"  {str(e)}\n"
                    f"  Retrying is disabled (use --retry-on-throttle to enable automatic retries)\n"
                )
                print(warning, file=sys.stderr)
                logging.warning(warning)
                raise ThrottledError(f"Test throttled (retries disabled): {str(e)}")

            # Check if we have retries left
            if attempt >= len(retry_delays):
                # No more retries
                warning = (
                    f"\n🚫 ERROR: Test {test_id} with model {model} failed after {attempt + 1} attempts due to throttling:\n"
                    f"  {str(e)}\n"
                    f"  All retry attempts exhausted.\n"
                )
                print(warning, file=sys.stderr)
                logging.error(warning)
                raise ThrottledError(
                    f"Test throttled after {attempt + 1} attempts: {str(e)}"
                )

            # We have retries left, wait and try again
            delay = retry_delays[attempt]
            warning = (
                f"\n⚠️  WARNING: Test {test_id} with model {model} encountered throttling/overload error (attempt {attempt + 1}/4):\n"
                f"  {str(e)}\n"
                f"  Retrying in {delay} seconds ({delay // 60} minutes)...\n"
                f"  (disable retries with --no-retry-on-throttle)\n"
            )
            print(warning, file=sys.stderr)
            print(warning, file=sys.stdout)  # Also to stdout for visibility
            logging.warning(warning)

            # Issue pytest warning
            warnings.warn(
                f"API throttling detected for {test_id!s} ({model!s}): retrying in {delay!s}s",
                UserWarning,
                stacklevel=2,
            )

            # Wait before retrying
            time.sleep(delay)
            total_retry_time += delay
            attempt += 1

    # Should never reach here, but just in case
    if last_error:
        raise ThrottledError(f"Test throttled after all retries: {str(last_error)}")
    raise RuntimeError("Unexpected state in retry_on_throttle")
