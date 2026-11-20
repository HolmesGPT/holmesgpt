"""Retry LLM calls that fail on provider rate limits, overload or 503s.

A retry repeats one LLM call, never the whole run, so tool work already done
in an investigation is kept.
"""

import contextlib
import email.utils
import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    Mapping,
    Optional,
    TypeVar,
)

import litellm
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    wait_random_exponential,
)

from holmes.common import env_vars

T = TypeVar("T")

RETRIES_KEY = "llm_rate_limit_retries"
WAIT_MS_KEY = "llm_rate_limit_wait_ms"

_backoff = wait_random_exponential(multiplier=2, min=2, max=60)

# Quota exhaustion also arrives as a 429 RateLimitError but does not clear by
# waiting. Matched: the Robusta relay's message, and the insufficient_quota code
# when a proxy keeps it in the message. litellm drops that code from OpenAI's and
# Azure's own errors, and their text ("exceeded your current quota") is not
# matched because Gemini uses the same words for its per-minute limits, so an
# out-of-credit OpenAI key is retried until the budget runs out.
_QUOTA_EXHAUSTED_MARKERS = (
    "account limit has been reached",
    "insufficient_quota",
)


class LLMRetryCancelled(Exception):
    """The caller's cancel event fired while backing off."""


@dataclass(frozen=True)
class _RetryScope:
    max_wait_seconds: Optional[float] = None
    cancel_event: Optional[threading.Event] = None
    shared_budget: Optional["_SharedBudget"] = None


class _SharedBudget:
    """Backoff time left for the LLM calls of a scope together, nested in any
    enclosing budget. Calls in a scope are expected to run one at a time."""

    def __init__(self, seconds: float, parent: Optional["_SharedBudget"]) -> None:
        self._remaining = seconds
        self._parent = parent

    @property
    def remaining(self) -> float:
        if self._parent is None:
            return self._remaining
        return min(self._remaining, self._parent.remaining)

    def spend(self, seconds: float) -> None:
        self._remaining = max(0.0, self._remaining - seconds)
        if self._parent is not None:
            self._parent.spend(seconds)


_retry_scope: ContextVar[_RetryScope] = ContextVar(
    "llm_rate_limit_retry_scope", default=_RetryScope()
)


@contextlib.contextmanager
def rate_limit_retry_scope(
    *,
    max_wait_seconds: Optional[float] = None,
    total_max_wait_seconds: Optional[float] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[None]:
    """Override the wait budget or make backoff cancellable for LLM calls made
    inside this block, without threading arguments through ``LLM.completion``.

    ``max_wait_seconds`` applies to each call; ``total_max_wait_seconds`` is one
    budget all calls in the block draw from. Settings left as None are inherited
    from an enclosing scope."""
    outer = _retry_scope.get()
    token = _retry_scope.set(
        _RetryScope(
            max_wait_seconds
            if max_wait_seconds is not None
            else outer.max_wait_seconds,
            cancel_event if cancel_event is not None else outer.cancel_event,
            _SharedBudget(total_max_wait_seconds, outer.shared_budget)
            if total_max_wait_seconds is not None
            else outer.shared_budget,
        )
    )
    try:
        yield
    finally:
        _retry_scope.reset(token)


def error_status_code(e: BaseException) -> Optional[int]:
    # litellm maps Anthropic's 529 to InternalServerError and overwrites the
    # status with 500; the error type in the message is all that survives.
    code = getattr(e, "status_code", None)
    if isinstance(code, str):
        code = int(code) if code.isdigit() else None
    if not isinstance(code, int):
        code = None
    if code in (None, 500) and "overloaded_error" in str(e):
        return 529
    return code


def is_rate_limit_error(e: BaseException) -> bool:
    # Bedrock raises a generic Exception with this text instead of RateLimitError.
    return isinstance(
        e, litellm.exceptions.RateLimitError
    ) or "Model is getting throttled" in str(e)


def is_quota_exhausted_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return any(marker in msg for marker in _QUOTA_EXHAUSTED_MARKERS)


def is_overloaded_error(e: BaseException) -> bool:
    return error_status_code(e) == 529


def is_service_unavailable_error(e: BaseException) -> bool:
    # litellm maps a provider 503 (Bedrock ServiceUnavailableException, OpenAI
    # "server is overloaded", relay's transient 503s) to ServiceUnavailableError.
    return error_status_code(e) == 503


def is_provider_capacity_error(e: BaseException) -> bool:
    """Rate limit, quota, overload or 503: the provider could not serve the
    request right now."""
    return (
        is_rate_limit_error(e)
        or is_overloaded_error(e)
        or is_service_unavailable_error(e)
    )


def is_retryable_llm_error(e: BaseException) -> bool:
    return is_provider_capacity_error(e) and not is_quota_exhausted_error(e)


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


def _wait(max_wait_seconds: float) -> Callable[[RetryCallState], float]:
    # Retry-After is a floor, not an exact time: a hint of 0 or a few ms must not
    # turn into a tight loop, and callers given the same hint must not wake together.
    # A jittered draw past the budget is cut to what is left, so the budget is
    # used up; a hint past it still fails fast.
    def wait(retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        hinted = retry_after_seconds(exc) if exc else None
        backoff = _backoff(retry_state)
        remaining = max_wait_seconds - retry_state.idle_for
        if remaining >= _backoff.min:
            backoff = min(backoff, remaining)
        return max(hinted or 0.0, backoff)

    return wait


def _stop(max_wait_seconds: float) -> Callable[[RetryCallState], bool]:
    # Only time spent backing off counts, not request time. Written so that a
    # NaN budget stops.
    def stop(retry_state: RetryCallState) -> bool:
        upcoming = retry_state.upcoming_sleep or 0.0
        within_budget = retry_state.idle_for + upcoming <= max_wait_seconds
        return max_wait_seconds <= 0 or not within_budget

    return stop


class _Sleeper:
    """tenacity sleep that wakes early on cancel and records the time actually
    slept (tenacity counts a cut-short sleep in full)."""

    def __init__(self, cancel_event: Optional[threading.Event]) -> None:
        self.cancel_event = cancel_event
        self.slept = 0.0

    def __call__(self, seconds: float) -> None:
        if self.cancel_event is None:
            time.sleep(seconds)
            self.slept += seconds
            return
        started = time.monotonic()
        if self.cancel_event.wait(seconds):
            self.slept += time.monotonic() - started
            raise LLMRetryCancelled()
        self.slept += seconds


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


def _annotate(target: Any, retries: int, wait_s: float) -> None:
    values = {RETRIES_KEY: retries, WAIT_MS_KEY: int(wait_s * 1000)}
    hidden = getattr(target, "_hidden_params", None)
    if isinstance(hidden, dict):
        hidden.update(values)
        return
    if isinstance(target, BaseException):
        for key, value in values.items():
            setattr(target, key, value)


def iter_exception_chain(error: BaseException) -> Iterator[BaseException]:
    """``error`` and the errors it was raised from, in order (``__cause__``, or
    ``__context__`` unless suppressed)."""
    seen = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )


def retry_counts(obj: Any) -> Dict[str, int]:
    """Retry counters recorded on an LLM response or a raised exception.

    For an exception, the chain is searched too, since callers re-raise the LLM
    error wrapped (``raise LLMInterruptedError() from e``).
    """
    keys = (RETRIES_KEY, WAIT_MS_KEY)
    hidden = getattr(obj, "_hidden_params", None)
    if isinstance(hidden, dict):
        return {key: int(hidden.get(key) or 0) for key in keys}
    if isinstance(obj, BaseException):
        for exc in iter_exception_chain(obj):
            counts = {key: int(getattr(exc, key, 0) or 0) for key in keys}
            if any(counts.values()):
                return counts
    return {key: 0 for key in keys}


def call_with_rate_limit_retry(fn: Callable[[], T], model: str) -> T:
    """Run ``fn``, retrying rate-limit / overloaded / 503 errors within the wait budget."""
    scope = _retry_scope.get()
    max_wait = (
        scope.max_wait_seconds
        if scope.max_wait_seconds is not None
        else env_vars.LLM_RATE_LIMIT_MAX_WAIT_SECONDS
    )
    if scope.shared_budget is not None:
        max_wait = min(max_wait, scope.shared_budget.remaining)
    sleeper = _Sleeper(scope.cancel_event)
    retrying = Retrying(
        retry=retry_if_exception(is_retryable_llm_error),
        wait=_wait(max_wait),
        stop=_stop(max_wait),
        sleep=sleeper,
        before_sleep=_log_retry(model),
        reraise=True,
    )
    attempts = 0
    try:
        for attempt in retrying:
            with attempt:
                attempts += 1
                result = fn()
    except Exception as e:
        _annotate(e, max(attempts - 1, 0), sleeper.slept)
        raise
    finally:
        if scope.shared_budget is not None:
            scope.shared_budget.spend(sleeper.slept)
    _annotate(result, attempts - 1, sleeper.slept)
    return result
