import email.utils
import threading
import time
from typing import List
from unittest.mock import MagicMock, patch

import httpx
import litellm
import pytest
from litellm.litellm_core_utils.streaming_handler import CustomStreamWrapper
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.common import env_vars
from holmes.core import llm_rate_limit
from holmes.core.llm import DefaultLLM
from holmes.core.llm_rate_limit import (
    LLMRetryCancelled,
    call_with_rate_limit_retry,
    is_quota_exhausted_error,
    rate_limit_retry_scope,
    error_status_code,
    is_overloaded_error,
    is_rate_limit_error,
    is_retryable_llm_error,
    is_service_unavailable_error,
    retry_after_seconds,
    retry_counts,
)
from holmes.core.llm_usage import RequestStats


def rate_limit(headers=None, message="Rate limit reached") -> litellm.RateLimitError:
    return litellm.RateLimitError(
        message=message,
        llm_provider="openai",
        model="gpt-4.1",
        response=httpx.Response(429, headers=headers or {}),
    )


def overloaded() -> litellm.InternalServerError:
    return litellm.InternalServerError(
        message='AnthropicError - {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}',
        llm_provider="anthropic",
        model="claude-sonnet-4-5",
    )


def unavailable() -> litellm.ServiceUnavailableError:
    # What litellm raises for a Bedrock ServiceUnavailableException.
    return litellm.ServiceUnavailableError(
        message='BedrockException - {"message": "Bedrock is unable to process your request."}',
        llm_provider="bedrock",
        model="anthropic.claude-sonnet-4-5-20250929-v1:0",
    )


def model_response() -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-test",
        choices=[
            Choices(
                index=0,
                message=Message(role="assistant", content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: List[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(time, "monotonic", fake.monotonic)
    monkeypatch.setattr(time, "sleep", fake.sleep)
    return fake


@pytest.fixture
def budget(monkeypatch):
    def set_budget(seconds: float) -> None:
        monkeypatch.setattr(env_vars, "LLM_RATE_LIMIT_MAX_WAIT_SECONDS", seconds)

    set_budget(180)
    return set_budget


def scripted(*outcomes):
    """A callable returning/raising ``outcomes`` in order, counting calls."""
    remaining = list(outcomes)
    calls = []

    def fn():
        calls.append(1)
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


class TestClassification:
    @pytest.mark.parametrize(
        "exc,retryable",
        [
            (rate_limit(), True),
            (overloaded(), True),
            (Exception("Model is getting throttled. Try your request again."), True),
            (
                litellm.InternalServerError(
                    message="boom", llm_provider="openai", model="m"
                ),
                False,
            ),
            (
                litellm.AuthenticationError(
                    message="bad key", llm_provider="openai", model="m"
                ),
                False,
            ),
            (unavailable(), True),
            (
                litellm.APIConnectionError(
                    message="connection reset", llm_provider="openai", model="m"
                ),
                False,
            ),
            (ValueError("unrelated"), False),
        ],
    )
    def test_retryable(self, exc, retryable):
        assert is_retryable_llm_error(exc) is retryable

    def test_service_unavailable_is_a_capacity_error(self):
        assert is_service_unavailable_error(unavailable())
        assert error_status_code(unavailable()) == 503
        assert not is_rate_limit_error(unavailable())
        assert not is_overloaded_error(unavailable())

    def test_rate_limit_vs_overloaded(self):
        assert is_rate_limit_error(rate_limit())
        assert not is_overloaded_error(rate_limit())
        assert is_overloaded_error(overloaded())
        assert not is_rate_limit_error(overloaded())

    def test_status_codes(self):
        assert error_status_code(rate_limit()) == 429
        assert error_status_code(overloaded()) == 529
        err = ValueError("x")
        assert error_status_code(err) is None
        err.status_code = "503"  # type: ignore[attr-defined]
        assert error_status_code(err) == 503
        err.status_code = "n/a"  # type: ignore[attr-defined]
        assert error_status_code(err) is None


class TestRetryAfter:
    def test_seconds(self):
        assert retry_after_seconds(rate_limit({"Retry-After": "7"})) == 7

    def test_milliseconds_win(self):
        exc = rate_limit({"retry-after-ms": "1500", "retry-after": "9"})
        assert retry_after_seconds(exc) == 1.5

    def test_invalid_milliseconds_fall_back_to_seconds(self):
        exc = rate_limit({"retry-after-ms": "soon", "retry-after": "4"})
        assert retry_after_seconds(exc) == 4

    def test_http_date(self):
        when = email.utils.formatdate(time.time() + 30, usegmt=True)
        assert 25 <= retry_after_seconds(rate_limit({"Retry-After": when})) <= 30

    def test_past_http_date_is_zero(self):
        when = email.utils.formatdate(time.time() - 30, usegmt=True)
        assert retry_after_seconds(rate_limit({"Retry-After": when})) == 0

    def test_negative_is_zero(self):
        assert retry_after_seconds(rate_limit({"Retry-After": "-3"})) == 0

    def test_garbage_is_ignored(self):
        assert retry_after_seconds(rate_limit({"Retry-After": "later"})) is None

    def test_missing(self):
        assert retry_after_seconds(rate_limit()) is None
        assert retry_after_seconds(ValueError("x")) is None

    def test_litellm_response_headers_fallback(self):
        exc = ValueError("x")
        exc.litellm_response_headers = {"Retry-After": "2"}  # type: ignore[attr-defined]
        assert retry_after_seconds(exc) == 2

    def test_unreadable_headers(self):
        exc = ValueError("x")
        exc.litellm_response_headers = object()  # type: ignore[attr-defined]
        assert retry_after_seconds(exc) is None


class TestCallWithRateLimitRetry:
    def test_two_429s_then_success_honours_retry_after(self, clock, budget):
        response = model_response()
        fn = scripted(
            rate_limit({"Retry-After": "3"}), rate_limit({"Retry-After": "5"}), response
        )

        assert call_with_rate_limit_retry(fn, model="m") is response

        assert len(fn.calls) == 3
        assert clock.sleeps == [3, 5]
        assert retry_counts(response) == {
            "llm_rate_limit_retries": 2,
            "llm_rate_limit_wait_ms": 8000,
        }

    def test_success_first_try_records_zero(self, clock, budget):
        response = model_response()
        assert call_with_rate_limit_retry(scripted(response), model="m") is response
        assert clock.sleeps == []
        assert retry_counts(response) == {
            "llm_rate_limit_retries": 0,
            "llm_rate_limit_wait_ms": 0,
        }

    def test_exponential_backoff_with_jitter_without_retry_after(self, clock, budget):
        fn = scripted(*[rate_limit() for _ in range(7)], "ok")
        assert call_with_rate_limit_retry(fn, model="m") == "ok"
        assert clock.sleeps[0] == 2
        for attempt, slept in enumerate(clock.sleeps, start=1):
            assert 2 <= slept <= min(60, 2 * 2 ** (attempt - 1))

    def test_overloaded_is_retried(self, clock, budget):
        fn = scripted(overloaded(), "ok")
        assert call_with_rate_limit_retry(fn, model="m") == "ok"
        assert len(fn.calls) == 2

    def test_budget_overrun_reraises_original_error(self, clock, budget):
        budget(10)
        last = rate_limit({"Retry-After": "4"})
        fn = scripted(
            rate_limit({"Retry-After": "4"}),
            rate_limit({"Retry-After": "4"}),
            last,
            "ok",
        )

        with pytest.raises(litellm.RateLimitError) as info:
            call_with_rate_limit_retry(fn, model="m")

        assert info.value is last
        assert len(fn.calls) == 3
        assert clock.sleeps == [4, 4]
        assert retry_counts(info.value) == {
            "llm_rate_limit_retries": 2,
            "llm_rate_limit_wait_ms": 8000,
        }

    def test_retry_after_beyond_budget_fails_without_waiting(self, clock, budget):
        fn = scripted(rate_limit({"Retry-After": "3600"}), "ok")
        with pytest.raises(litellm.RateLimitError):
            call_with_rate_limit_retry(fn, model="m")
        assert len(fn.calls) == 1
        assert clock.sleeps == []

    def test_zero_budget_disables_retries(self, clock, budget):
        budget(0)
        fn = scripted(rate_limit({"Retry-After": "0"}), "ok")
        with pytest.raises(litellm.RateLimitError):
            call_with_rate_limit_retry(fn, model="m")
        assert len(fn.calls) == 1

    def test_non_retryable_error_is_raised_immediately(self, clock, budget):
        error = litellm.AuthenticationError(
            message="bad key", llm_provider="openai", model="m"
        )
        fn = scripted(error, "ok")
        with pytest.raises(litellm.AuthenticationError):
            call_with_rate_limit_retry(fn, model="m")
        assert len(fn.calls) == 1
        assert retry_counts(error)["llm_rate_limit_retries"] == 0

    def test_retry_counts_default_to_zero(self):
        assert retry_counts(ValueError("x")) == {
            "llm_rate_limit_retries": 0,
            "llm_rate_limit_wait_ms": 0,
        }
        assert retry_counts(object()) == {
            "llm_rate_limit_retries": 0,
            "llm_rate_limit_wait_ms": 0,
        }


def _make_llm() -> DefaultLLM:
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = "openai/gpt-4.1"
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = {}
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = None
    return llm


class TestDefaultLLMCompletion:
    def test_rate_limited_completion_is_retried(self, clock, budget):
        response = model_response()
        with patch(
            "holmes.core.llm.litellm.completion",
            side_effect=[rate_limit({"Retry-After": "3"}), response],
        ) as completion:
            result = _make_llm().completion(
                messages=[{"role": "user", "content": "hi"}]
            )

        assert result is response
        assert completion.call_count == 2
        assert clock.sleeps == [3]
        stats = RequestStats.from_response(result)
        assert stats.llm_rate_limit_retries == 1
        assert stats.llm_rate_limit_wait_ms == 3000

    def test_error_opening_a_stream_is_retried(self, clock, budget):
        stream = MagicMock(spec=CustomStreamWrapper)
        with patch(
            "holmes.core.llm.litellm.completion",
            side_effect=[rate_limit({"Retry-After": "1"}), stream],
        ) as completion:
            result = _make_llm().completion(
                messages=[{"role": "user", "content": "hi"}], stream=True
            )
        assert result is stream
        assert completion.call_count == 2

    def test_mid_stream_429_is_not_retried(self, clock, budget):
        stream = MagicMock(spec=CustomStreamWrapper)
        stream.__iter__.side_effect = rate_limit({"Retry-After": "1"})
        with patch(
            "holmes.core.llm.litellm.completion", return_value=stream
        ) as completion:
            result = _make_llm().completion(
                messages=[{"role": "user", "content": "hi"}], stream=True
            )
            with pytest.raises(litellm.RateLimitError):
                list(result)

        assert completion.call_count == 1
        assert clock.sleeps == []

    def test_budget_overrun_propagates_from_completion(self, clock, budget):
        budget(5)
        with patch(
            "holmes.core.llm.litellm.completion",
            side_effect=[rate_limit({"Retry-After": "3"})] * 3,
        ) as completion:
            with pytest.raises(litellm.RateLimitError) as info:
                _make_llm().completion(messages=[{"role": "user", "content": "hi"}])
        assert completion.call_count == 2
        assert retry_counts(info.value)["llm_rate_limit_retries"] == 1


class TestRequestStatsRetryCounters:
    def test_from_response_reads_counters(self):
        response = model_response()
        response._hidden_params["llm_rate_limit_retries"] = 3
        response._hidden_params["llm_rate_limit_wait_ms"] = 4200
        stats = RequestStats.from_response(response)
        assert stats.llm_rate_limit_retries == 3
        assert stats.llm_rate_limit_wait_ms == 4200

    def test_from_unreadable_response_keeps_counters(self):
        response = model_response()
        response._hidden_params["llm_rate_limit_retries"] = 1
        with patch(
            "holmes.core.llm_usage.extract_usage_from_response",
            side_effect=KeyError("usage"),
        ):
            stats = RequestStats.from_response(response)
        assert stats.llm_rate_limit_retries == 1

    def test_counters_accumulate_without_tokens(self):
        total = RequestStats(total_tokens=10, llm_rate_limit_retries=1)
        total += RequestStats(llm_rate_limit_retries=2, llm_rate_limit_wait_ms=500)
        assert total.llm_rate_limit_retries == 3
        assert total.llm_rate_limit_wait_ms == 500
        assert total.total_tokens == 10


class TestQuotaExhaustion:
    @pytest.mark.parametrize(
        "message",
        [
            # Robusta relay, relay/pkg/common/relay_error_codes.py
            "Your Robusta AI account limit has been reached. Contact support@robusta.dev to increase limits.",
            # the error code, when a proxy keeps it in the message
            "OpenAIException - insufficient_quota",
        ],
    )
    def test_quota_exhaustion_is_not_retried(self, clock, budget, message):
        exc = rate_limit({"Retry-After": "1"}, message=message)
        assert is_rate_limit_error(exc)
        assert is_quota_exhausted_error(exc)
        assert not is_retryable_llm_error(exc)

        fn = scripted(exc, "ok")
        with pytest.raises(litellm.RateLimitError):
            call_with_rate_limit_retry(fn, model="m")
        assert len(fn.calls) == 1
        assert clock.sleeps == []

    def test_ordinary_rate_limit_is_not_quota(self):
        assert not is_quota_exhausted_error(rate_limit())

    def test_gemini_per_minute_limit_is_retried(self):
        # Gemini words its RPM/TPM 429 like OpenAI's insufficient_quota.
        exc = rate_limit(
            message="You exceeded your current quota, please check your plan and "
            "billing details. For more information on this error, head to: "
            "https://ai.google.dev/gemini-api/docs/rate-limits."
        )
        assert is_retryable_llm_error(exc)


class TestWaitAndBudget:
    def test_zero_retry_after_still_backs_off(self, clock, budget):
        fn = scripted(
            rate_limit({"Retry-After": "0"}), rate_limit({"retry-after-ms": "20"}), "ok"
        )
        assert call_with_rate_limit_retry(fn, model="m") == "ok"
        assert all(slept >= 2 for slept in clock.sleeps)

    def test_retry_after_is_a_floor(self, clock, budget):
        fn = scripted(rate_limit({"Retry-After": "7"}), "ok")
        call_with_rate_limit_retry(fn, model="m")
        assert clock.sleeps == [7]

    def test_request_time_does_not_count_towards_budget(self, clock, budget):
        budget(10)
        outcomes = [
            rate_limit({"Retry-After": "4"}),
            rate_limit({"Retry-After": "4"}),
            "ok",
        ]

        def slow_call():
            clock.now += 100  # each request takes 100s
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        assert call_with_rate_limit_retry(slow_call, model="m") == "ok"
        assert clock.sleeps == [4, 4]


class TestRetryScope:
    def test_scope_overrides_budget_and_resets(self, clock, budget):
        with rate_limit_retry_scope(max_wait_seconds=0):
            fn = scripted(rate_limit({"Retry-After": "1"}), "ok")
            with pytest.raises(litellm.RateLimitError):
                call_with_rate_limit_retry(fn, model="m")
            assert len(fn.calls) == 1

        fn = scripted(rate_limit({"Retry-After": "1"}), "ok")
        assert call_with_rate_limit_retry(fn, model="m") == "ok"

    def test_cancel_event_interrupts_backoff(self, budget):
        cancel = threading.Event()
        fn = scripted(rate_limit({"Retry-After": "30"}), "ok")
        threading.Timer(0.1, cancel.set).start()

        started = time.monotonic()
        with rate_limit_retry_scope(cancel_event=cancel):
            with pytest.raises(LLMRetryCancelled):
                call_with_rate_limit_retry(fn, model="m")

        assert time.monotonic() - started < 5
        assert len(fn.calls) == 1

    def test_unset_cancel_event_lets_retries_run(self, clock, budget):
        with rate_limit_retry_scope(cancel_event=threading.Event()):
            fn = scripted(rate_limit(), "ok")
            assert call_with_rate_limit_retry(fn, model="m") == "ok"


class TestCancellationAndAccounting:
    def test_nan_budget_stops(self, clock, budget):
        budget(float("nan"))
        fn = scripted(rate_limit(), "ok")
        with pytest.raises(litellm.RateLimitError):
            call_with_rate_limit_retry(fn, model="m")
        assert len(fn.calls) == 1

    def test_cancelled_backoff_records_time_actually_waited(self, budget):
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()
        with rate_limit_retry_scope(cancel_event=cancel):
            with pytest.raises(LLMRetryCancelled) as info:
                call_with_rate_limit_retry(
                    scripted(rate_limit({"Retry-After": "30"})), model="m"
                )
        assert retry_counts(info.value)["llm_rate_limit_retries"] == 0
        assert retry_counts(info.value)["llm_rate_limit_wait_ms"] < 5000

    def test_retry_counts_ignore_non_exception_objects(self):
        assert retry_counts(MagicMock()) == {
            "llm_rate_limit_retries": 0,
            "llm_rate_limit_wait_ms": 0,
        }
        assert RequestStats.from_response(MagicMock()).llm_rate_limit_retries == 0

    def test_retry_counts_follow_the_exception_chain(self):
        cause = rate_limit()
        cause.llm_rate_limit_retries = 3  # type: ignore[attr-defined]
        cause.llm_rate_limit_wait_ms = 9000  # type: ignore[attr-defined]
        try:
            try:
                raise cause
            except litellm.RateLimitError as e:
                raise RuntimeError("wrapped") from e
        except RuntimeError as wrapped:
            assert retry_counts(wrapped) == {
                "llm_rate_limit_retries": 3,
                "llm_rate_limit_wait_ms": 9000,
            }


def test_nested_scopes_inherit_unset_settings(clock, budget):
    cancel = threading.Event()
    with rate_limit_retry_scope(cancel_event=cancel):
        with rate_limit_retry_scope(max_wait_seconds=0):
            scope = llm_rate_limit._retry_scope.get()
            assert scope.cancel_event is cancel
            assert scope.max_wait_seconds == 0
        assert llm_rate_limit._retry_scope.get().max_wait_seconds is None


def test_overloaded_text_on_another_status_is_not_overload():
    error = litellm.BadRequestError(
        message="cannot parse log line: overloaded_error",
        model="m",
        llm_provider="openai",
    )
    assert error_status_code(error) == 400
    assert not is_retryable_llm_error(error)


def test_azure_ad_token_is_fetched_per_attempt(clock, budget):
    llm = _make_llm()
    llm.model = "azure/gpt-4o"
    with patch("holmes.core.llm.AZURE_AD_TOKEN_AUTH", True), patch(
        "holmes.core.llm.get_azure_ad_token", side_effect=["token-1", "token-2"]
    ), patch(
        "holmes.core.llm.litellm.completion",
        side_effect=[rate_limit({"Retry-After": "3"}), model_response()],
    ) as completion:
        llm.completion(messages=[{"role": "user", "content": "hi"}])
    tokens = [c.kwargs["azure_ad_token"] for c in completion.call_args_list]
    assert tokens == ["token-1", "token-2"]


class TestServiceUnavailable:
    def test_bedrock_503s_then_success(self, clock, budget):
        response = model_response()
        llm = _make_llm()
        llm.model = "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0"
        with patch(
            "holmes.core.llm.litellm.completion",
            side_effect=[unavailable(), unavailable(), response],
        ) as completion:
            result = llm.completion(messages=[{"role": "user", "content": "hi"}])

        assert result is response
        assert completion.call_count == 3
        assert len(clock.sleeps) == 2
        assert RequestStats.from_response(result).llm_rate_limit_retries == 2

    def test_persistent_503_reraises_after_budget(self, clock, budget):
        budget(10)
        fn = scripted(*[unavailable() for _ in range(20)])
        with pytest.raises(litellm.ServiceUnavailableError) as info:
            call_with_rate_limit_retry(fn, model="m")
        assert sum(clock.sleeps) <= 10
        assert retry_counts(info.value)["llm_rate_limit_retries"] == len(fn.calls) - 1


def test_total_budget_is_shared_by_calls_in_a_scope(clock, budget):
    with rate_limit_retry_scope(total_max_wait_seconds=10):
        first = scripted(
            rate_limit({"Retry-After": "4"}), rate_limit({"Retry-After": "4"}), "ok"
        )
        assert call_with_rate_limit_retry(first, model="m") == "ok"
        # 8 of the 10 seconds are spent; the next call cannot wait 4 more.
        second = scripted(rate_limit({"Retry-After": "4"}), "ok")
        with pytest.raises(litellm.RateLimitError):
            call_with_rate_limit_retry(second, model="m")
    assert clock.sleeps == [4, 4]
    # Outside the scope the per-call budget applies again.
    assert call_with_rate_limit_retry(scripted(rate_limit(), "ok"), model="m") == "ok"


def test_nested_total_budget_draws_from_the_outer_one(clock, budget):
    with rate_limit_retry_scope(total_max_wait_seconds=10):
        with rate_limit_retry_scope(total_max_wait_seconds=180):
            first = scripted(
                rate_limit({"Retry-After": "4"}), rate_limit({"Retry-After": "4"}), "ok"
            )
            assert call_with_rate_limit_retry(first, model="m") == "ok"
            second = scripted(rate_limit({"Retry-After": "4"}), "ok")
            with pytest.raises(litellm.RateLimitError):
                call_with_rate_limit_retry(second, model="m")
        assert llm_rate_limit._retry_scope.get().shared_budget.remaining == 2
    assert clock.sleeps == [4, 4]
