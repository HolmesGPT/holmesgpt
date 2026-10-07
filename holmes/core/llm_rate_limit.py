"""Retry LLM calls that fail on provider rate limits or overload.

A retry repeats one LLM call, never the whole run, so tool work already done
in an investigation is kept.
"""

import contextlib
import email.utils
import logging
import threading
import time
from typing import Any, Callable, ContextManager, Dict, Mapping, Optional, TypeVar

import litellm
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_before_delay,
    wait_random_exponential,
)

from holmes.common import env_vars

T = TypeVar("T")

RETRIES_KEY = "llm_rate_limit_retries"
WAIT_MS_KEY = "llm_rate_limit_wait_ms"

_backoff = wait_random_exponential(multiplier=2, min=2, max=60)

_model_semaphores: Dict[str, threading.BoundedSemaphore] = {}
_model_semaphores_lock = threading.Lock()


def is_rate_limit_error(e: BaseException) -> bool:
    # Bedrock raises a generic Exception with this text instead of RateLimitError.
    return isinstance(
        e, litellm.exceptions.RateLimitError
    ) or "Model is getting throttled" in str(e)


def is_overloaded_error(e: BaseException) -> bool:
    return error_status_code(e) == 529


def is_retryable_llm_error(e: BaseException) -> bool:
    return is_rate_limit_error(e) or is_overloaded_error(e)


def error_status_code(e: BaseException) -> Optional[int]:
    # litellm maps Anthropic's 529 to InternalServerError and overwrites the
    # status with 500; the error type in the message is all that survives.
    if "overloaded_error" in str(e):
        return 529
    code = getattr(e, "status_code", None)
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.isdigit():
        return int(code)
    return None


def _headers(e: BaseException) -> Mapping[str, str]:
    headers = getattr(getattr(e, "response", None), "headers", None) or getattr(
        e, "litellm_response_headers", None
    )
    if not headers:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in headers.items()}
    except Exception:
        return {}


def retry_after_seconds(e: BaseException) -> Optional[float]:
    """Seconds the provider asked us to wait, from Retry-After(-ms) headers."""
    headers = _headers(e)
    ms = headers.get("retry-after-ms")
    if ms is not None:
        try:
            return max(0.0, float(ms) / 1000)
        except ValueError:
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    hinted = retry_after_seconds(exc) if exc else None
    return hinted if hinted is not None else _backoff(retry_state)


def _log_retry(model: str) -> Callable[[RetryCallState], None]:
    def log(retry_state: RetryCallState) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        logging.warning(
            "LLM call to %s failed with %s (status %s); retry %d in %.1fs",
            model,
            type(exc).__name__,
            error_status_code(exc) if exc else None,
            retry_state.attempt_number,
            retry_state.upcoming_sleep,
        )

    return log


def _model_slot(model: str) -> ContextManager[Any]:
    cap = env_vars.LLM_MAX_CONCURRENT_CALLS_PER_MODEL
    if cap <= 0:
        return contextlib.nullcontext()
    with _model_semaphores_lock:
        semaphore = _model_semaphores.get(model)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(cap)
            _model_semaphores[model] = semaphore
    return semaphore


def _annotate(target: Any, retries: int, wait_s: float) -> None:
    values = {RETRIES_KEY: retries, WAIT_MS_KEY: int(wait_s * 1000)}
    hidden = getattr(target, "_hidden_params", None)
    if isinstance(hidden, dict):
        hidden.update(values)
        return
    if isinstance(target, BaseException):
        for key, value in values.items():
            setattr(target, key, value)


def retry_counts(obj: Any) -> Dict[str, int]:
    """Retry counters recorded on an LLM response or a raised exception."""
    hidden = getattr(obj, "_hidden_params", None)
    if isinstance(hidden, dict):
        return {key: int(hidden.get(key) or 0) for key in (RETRIES_KEY, WAIT_MS_KEY)}
    return {key: int(getattr(obj, key, 0) or 0) for key in (RETRIES_KEY, WAIT_MS_KEY)}


def call_with_rate_limit_retry(fn: Callable[[], T], model: str) -> T:
    """Run ``fn``, retrying rate-limit / overloaded errors within the wait budget.

    The per-model concurrency slot is held only while a request is in flight,
    not while backing off.
    """
    retrying = Retrying(
        retry=retry_if_exception(is_retryable_llm_error),
        wait=_wait,
        stop=stop_before_delay(env_vars.LLM_RATE_LIMIT_MAX_WAIT_SECONDS),
        before_sleep=_log_retry(model),
        reraise=True,
    )
    try:
        for attempt in retrying:
            with attempt, _model_slot(model):
                result = fn()
    except Exception as e:
        _annotate(
            e,
            retrying.statistics.get("attempt_number", 1) - 1,
            retrying.statistics.get("idle_for", 0.0),
        )
        raise
    _annotate(
        result,
        retrying.statistics.get("attempt_number", 1) - 1,
        retrying.statistics.get("idle_for", 0.0),
    )
    return result
