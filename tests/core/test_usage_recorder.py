"""Unit tests for holmes.core.usage_recorder.

The recorder is fire-and-forget — it spawns a daemon thread to write the row.
For deterministic tests we patch threading.Thread so the target runs inline,
which lets us assert against the exact UsageRecorderState passed to
dal.record_usage_event.

Per Moshe's review on PR #1969, ``record_usage_event`` now takes the entire
``UsageRecorderState`` positionally instead of ~20 individual kwargs (drops
the old ``to_kwargs()`` indirection — the DAL is the single place that
knows the column shape). Tests assert on
``state.dal.record_usage_event.call_args.args[0]``, which is the live state
object the recorder passed in.
"""

from typing import List
from unittest.mock import MagicMock

import pytest

from holmes.core.usage_recorder import (
    UsageRecorderState,
    record_error,
    record_from_llm_result,
    stream_with_usage_recording,
)
from holmes.utils.stream import StreamEvents, StreamMessage


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_state(**overrides) -> UsageRecorderState:
    """Build a UsageRecorderState with sensible defaults for tests."""
    base = dict(
        dal=MagicMock(enabled=True),
        request_type="user_chat",
        model="openai/gpt-4",
        provider="openai",
        is_robusta_model=False,
        request_source="freeform",
        source_ref=None,
        conversation_id="conv-123",
        conversation_source="chat_history",
        user_id="user-abc",
        is_streaming=True,
    )
    base.update(overrides)
    return UsageRecorderState(**base)


def _stream(*events: StreamMessage):
    for e in events:
        yield e


def _terminal_data(costs: dict, num_llm_calls: int = 1, finish_reason: str = "stop") -> dict:
    return {
        "content": "ok",
        "messages": [],
        "metadata": {"costs": costs, "finish_reason": finish_reason},
        "num_llm_calls": num_llm_calls,
        "costs": costs,
    }


def _patch_inline_thread(monkeypatch):
    """Replace the recorder's ThreadPoolExecutor with an inline stub so
    target(state) runs synchronously in the test, letting us assert
    against state.dal.record_usage_event right after _fire returns.

    The stub mimics ``ThreadPoolExecutor.submit`` semantics: if the
    callable raises, the exception is captured (not propagated) — the
    real executor would put it on the returned Future, which production
    code never awaits. Without this, the inline path would bubble
    target exceptions that the production path never would, breaking
    "fire-and-forget never propagates" tests.

    Kept under the historical name (_patch_inline_thread) so existing
    test bodies don't churn — the underlying mechanism is now the
    bounded executor at module-level rather than a fresh Thread per call.
    """
    import holmes.core.usage_recorder as mod

    class _InlineExecutor:
        def submit(self, fn, *args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception:
                # Real executor stashes the exception on the Future. We
                # don't return one, so just swallow — the test doesn't
                # await results.
                pass

    monkeypatch.setattr(mod, "_RECORDER_EXECUTOR", _InlineExecutor())


def _state_arg(state: UsageRecorderState) -> UsageRecorderState:
    """Pull the state object out of the recorded dal.record_usage_event call.

    The recorder fires it positionally — args[0]. Centralized so test bodies
    don't repeat the indexing ceremony.
    """
    return state.dal.record_usage_event.call_args.args[0]


# ──────────────────────────────────────────────────────────────────
# UsageRecorderState basics — direct attribute access + duration_ms property
# (Replaces the old TestToKwargs class; to_kwargs() no longer exists.)
# ──────────────────────────────────────────────────────────────────


class TestStateBasics:
    def test_default_values_match_spec(self):
        state = _make_state()
        # Identity
        assert state.request_type == "user_chat"
        assert state.request_source == "freeform"
        assert state.conversation_id == "conv-123"
        assert state.conversation_source == "chat_history"
        assert state.user_id == "user-abc"
        assert state.request_id  # auto-generated UUID

        # Classification
        assert state.model == "openai/gpt-4"
        assert state.provider == "openai"
        assert state.is_robusta_model is False
        assert state.is_streaming is True

        # Mutable defaults — these get filled by the wrapper at runtime
        assert state.status == "success"  # RequestStatus.SUCCESS == "success"
        assert state.iterations == 0
        assert state.tool_call_count == 0
        assert state.finish_reason is None
        assert state.meta == {}
        assert state.stats is None  # not pre-populated

    def test_duration_ms_property_grows_with_time(self):
        state = _make_state()
        # Force t_start to be in the past so duration_ms > 0
        state.t_start -= 1.0
        assert state.duration_ms >= 1000

    def test_duration_ms_is_an_int(self):
        # The DB column is `int`; the property must always return an int.
        state = _make_state()
        assert isinstance(state.duration_ms, int)

    def test_is_internal_defaults_to_false(self):
        state = _make_state()
        assert state.is_internal is False

    def test_is_internal_round_trips(self):
        state = _make_state(is_internal=True)
        assert state.is_internal is True


# ──────────────────────────────────────────────────────────────────
# stream_with_usage_recording
# ──────────────────────────────────────────────────────────────────


class TestStreamWithUsageRecording:
    def test_success_path_records_with_status_success(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        costs = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "total_cost": 0.001}
        events = [
            StreamMessage(event=StreamEvents.START_TOOL, data={}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "kubectl"}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "prom"}),
            StreamMessage(event=StreamEvents.ANSWER_END, data=_terminal_data(costs, num_llm_calls=3)),
        ]

        # Drain the wrapped stream — the recorder fires in the finally block.
        consumed: List[StreamMessage] = list(
            stream_with_usage_recording(_stream(*events), state)
        )

        assert len(consumed) == 4
        state.dal.record_usage_event.assert_called_once()
        s = _state_arg(state)
        assert s.status == "success"
        assert s.tool_call_count == 2
        assert s.iterations == 3
        assert s.finish_reason == "stop"
        assert s.stats.prompt_tokens == 100
        assert s.stats.total_tokens == 150

    def test_error_event_marks_status_error(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        events = [
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "k"}),
            StreamMessage(event=StreamEvents.ERROR, data={"metadata": {}}),
        ]
        list(stream_with_usage_recording(_stream(*events), state))

        s = _state_arg(state)
        assert s.status == "error"
        assert s.tool_call_count == 1

    def test_approval_required_marks_status_approval_required(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        list(stream_with_usage_recording(
            _stream(StreamMessage(event=StreamEvents.APPROVAL_REQUIRED, data={"metadata": {}})),
            state,
        ))
        assert _state_arg(state).status == "approval_required"

    def test_exception_in_inner_stream_still_fires_recorder_with_error_status(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        def failing_stream():
            yield StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "x"})
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            list(stream_with_usage_recording(failing_stream(), state))

        # Recorder still fires from the finally
        state.dal.record_usage_event.assert_called_once()
        s = _state_arg(state)
        assert s.status == "error"
        # And the tool we saw before the exception was counted
        assert s.tool_call_count == 1

    def test_token_count_event_captures_cumulative_costs(self, monkeypatch):
        """TOKEN_COUNT events broadcast the running cost after each
        successful LLM iteration. The wrapper must capture them so partial
        spend is recorded even when the agentic loop raises mid-loop
        (call_stream's local stats accumulator gets GC'd on exception)."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        # Two successful iterations broadcast their cumulative cost...
        iter1_costs = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "total_cost": 0.001}
        iter2_costs = {"prompt_tokens": 250, "completion_tokens": 50, "total_tokens": 300, "total_cost": 0.003}

        events = [
            StreamMessage(event=StreamEvents.TOKEN_COUNT, data={"metadata": {"costs": iter1_costs}}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "k"}),
            StreamMessage(event=StreamEvents.TOKEN_COUNT, data={"metadata": {"costs": iter2_costs}}),
            StreamMessage(event=StreamEvents.ANSWER_END, data=_terminal_data(iter2_costs, num_llm_calls=2)),
        ]
        list(stream_with_usage_recording(_stream(*events), state))

        s = _state_arg(state)
        # Last cumulative wins — ANSWER_END's costs override the earlier
        # TOKEN_COUNT (in this case they're identical, mirroring real flow).
        assert s.stats.total_tokens == 300
        assert s.stats.prompt_tokens == 250
        assert s.stats.total_cost == pytest.approx(0.003)

    def test_partial_costs_captured_when_loop_raises_mid_iteration(self, monkeypatch):
        """The whole point of listening to TOKEN_COUNT: when the agentic
        loop hits an exception in iteration 3 after iterations 1 and 2
        succeeded, the recorder should still record the cost of iters 1+2.
        Without TOKEN_COUNT capture this row would have status=error and
        zero tokens, even though real spend already happened."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        # Cumulative costs after iterations 1 and 2 succeeded.
        partial_costs = {
            "prompt_tokens": 1500,
            "completion_tokens": 200,
            "total_tokens": 1700,
            "total_cost": 0.012,
        }

        def failing_stream():
            # Iteration 1 succeeds.
            yield StreamMessage(event=StreamEvents.TOKEN_COUNT, data={"metadata": {"costs": {"prompt_tokens": 800, "total_tokens": 850, "total_cost": 0.005}}})
            yield StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "kubectl"})
            # Iteration 2 succeeds.
            yield StreamMessage(event=StreamEvents.TOKEN_COUNT, data={"metadata": {"costs": partial_costs}})
            yield StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "prom"})
            # Iteration 3's LLM call raises — TOKEN_COUNT for iter 3 is
            # never emitted; the exception unwinds call_stream's frame.
            raise RuntimeError("rate limit hit")

        with pytest.raises(RuntimeError, match="rate limit"):
            list(stream_with_usage_recording(failing_stream(), state))

        s = _state_arg(state)
        # Partial spend from iterations 1+2 was captured — NOT zero.
        assert s.stats is not None
        assert s.stats.total_tokens == 1700
        assert s.stats.prompt_tokens == 1500
        assert s.stats.total_cost == pytest.approx(0.012)
        # Status correctly marked as error.
        assert s.status == "error"
        # Tool calls before the exception were counted.
        assert s.tool_call_count == 2

    def test_partial_costs_captured_on_client_disconnect(self, monkeypatch):
        """Client-disconnect case: stream ends cleanly without a terminal
        event after some TOKEN_COUNT events. Recorder marks 'aborted' but
        still captures the partial spend so the row reflects real cost."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        partial_costs = {
            "prompt_tokens": 600,
            "completion_tokens": 100,
            "total_tokens": 700,
            "total_cost": 0.004,
        }
        events = [
            StreamMessage(event=StreamEvents.TOKEN_COUNT, data={"metadata": {"costs": partial_costs}}),
            StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "k"}),
            # Stream just ends — no terminal event (client closed the tab).
        ]
        list(stream_with_usage_recording(_stream(*events), state))

        s = _state_arg(state)
        # Aborted, but with the real partial cost.
        assert s.status == "aborted"
        assert s.stats is not None
        assert s.stats.total_tokens == 700
        assert s.stats.total_cost == pytest.approx(0.004)

    def test_stream_without_terminal_event_still_records_as_aborted(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        # No terminal — e.g. client disconnected.
        list(stream_with_usage_recording(
            _stream(StreamMessage(event=StreamEvents.TOOL_RESULT, data={"tool_name": "y"})),
            state,
        ))

        # finally block still fired, but status downgraded from default
        # "success" to "aborted" because no terminal event was seen.
        state.dal.record_usage_event.assert_called_once()
        assert _state_arg(state).status == "aborted"

    def test_terminal_event_keeps_its_explicit_status(self, monkeypatch):
        """Sanity: the abort downgrade only applies when no terminal was seen."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        list(stream_with_usage_recording(
            _stream(StreamMessage(
                event=StreamEvents.ANSWER_END,
                data={"metadata": {}, "num_llm_calls": 1},
            )),
            state,
        ))

        assert _state_arg(state).status == "success"

    def test_request_id_injected_into_answer_end_metadata(self, monkeypatch):
        """The FE needs request_id from ai_answer_end so it can post feedback later."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "deadbeef-1234"

        answer_end = StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={
                "content": "ok",
                "messages": [],
                "metadata": {"costs": {"total_tokens": 100}},
                "num_llm_calls": 1,
            },
        )
        consumed = list(stream_with_usage_recording(_stream(answer_end), state))

        # The same StreamMessage flows through; its metadata now has request_id.
        out = consumed[0]
        assert out.event == StreamEvents.ANSWER_END
        assert out.data["metadata"]["request_id"] == "deadbeef-1234"
        # Existing metadata content (costs) is preserved alongside.
        assert out.data["metadata"]["costs"] == {"total_tokens": 100}

    def test_request_id_injected_when_metadata_missing(self, monkeypatch):
        """If the upstream event has no metadata key, _inject_request_id creates it."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "uuid-xyz"

        answer_end = StreamMessage(
            event=StreamEvents.ANSWER_END,
            data={"content": "ok", "messages": [], "num_llm_calls": 1},  # no metadata key
        )
        consumed = list(stream_with_usage_recording(_stream(answer_end), state))
        assert consumed[0].data["metadata"] == {"request_id": "uuid-xyz"}

    def test_request_id_injected_into_approval_required(self, monkeypatch):
        """Feedback should be possible on paused turns too — request_id must be there."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "rid-paused"

        approval = StreamMessage(
            event=StreamEvents.APPROVAL_REQUIRED,
            data={"metadata": {}, "pending_approvals": []},
        )
        consumed = list(stream_with_usage_recording(_stream(approval), state))
        assert consumed[0].data["metadata"]["request_id"] == "rid-paused"

    def test_request_id_injected_into_error_event(self, monkeypatch):
        """Surface request_id even on ERROR so the FE can report 'this request failed'."""
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        state.request_id = "rid-err"

        err = StreamMessage(event=StreamEvents.ERROR, data={"metadata": {}})
        consumed = list(stream_with_usage_recording(_stream(err), state))
        assert consumed[0].data["metadata"]["request_id"] == "rid-err"


# ──────────────────────────────────────────────────────────────────
# record_from_llm_result (non-streaming)
# ──────────────────────────────────────────────────────────────────


class TestRecordFromLlmResult:
    def test_extracts_stats_iterations_finish_reason_and_tool_count(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state(is_streaming=False)

        # Build a fake LLMResult-shaped object. RequestStats fields are inherited,
        # so we can dump a flat dict via model_dump() in the helper.
        fake_result = MagicMock()
        fake_result.model_dump.return_value = {
            "total_cost": 0.005,
            "total_tokens": 250,
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "cached_tokens": None,
            "reasoning_tokens": 0,
            "max_completion_tokens_per_call": 50,
            "max_prompt_tokens_per_call": 200,
            "num_compactions": 0,
        }
        fake_result.num_llm_calls = 4
        fake_result.tool_calls = [object(), object(), object()]
        fake_result.finish_reason = "stop"

        record_from_llm_result(state, fake_result)

        s = _state_arg(state)
        assert s.status == "success"
        assert s.iterations == 4
        assert s.tool_call_count == 3
        assert s.finish_reason == "stop"
        assert s.stats.total_tokens == 250
        assert s.stats.total_cost == 0.005

    def test_handles_missing_attrs_gracefully(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        # An LLMResult-like object with only the bare minimum
        bare = MagicMock()
        bare.model_dump.return_value = {}
        bare.num_llm_calls = None
        bare.tool_calls = None
        bare.finish_reason = None

        record_from_llm_result(state, bare)

        s = _state_arg(state)
        # iterations falls back to 1 when num_llm_calls is None
        assert s.iterations == 1
        assert s.tool_call_count == 0


# ──────────────────────────────────────────────────────────────────
# record_error
# ──────────────────────────────────────────────────────────────────


class TestRecordError:
    def test_marks_rate_limited_when_rate_limit_in_message(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        record_error(state, RuntimeError("rate limit exceeded for model"))
        assert _state_arg(state).status == "rate_limited"

    def test_marks_error_for_other_exceptions(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        record_error(state, ValueError("invalid model"))
        assert _state_arg(state).status == "error"


# ──────────────────────────────────────────────────────────────────
# Disabled-DAL no-op behavior
# ──────────────────────────────────────────────────────────────────


class TestDisabledDalNoop:
    def test_no_thread_spawned_when_dal_disabled(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state(dal=MagicMock(enabled=False))
        record_error(state, RuntimeError("anything"))
        # The disabled DAL has the method mocked but should never be called.
        state.dal.record_usage_event.assert_not_called()

    def test_no_thread_spawned_when_dal_is_none(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state(dal=None)
        # No exception; nothing happens.
        record_error(state, RuntimeError("x"))


# ──────────────────────────────────────────────────────────────────
# Real executor mode (no inline patching) — verifies fire-and-forget
# ──────────────────────────────────────────────────────────────────


class TestFireAndForgetThreadMode:
    def test_record_runs_dal_in_background_via_executor(self):
        """The shared ThreadPoolExecutor runs record_usage_event off the
        caller's thread. The caller returns before the dal write completes."""
        import threading
        import time

        called = threading.Event()

        # Real executor → state arrives positionally. Match the
        # production call signature.
        def slow_record(state):
            time.sleep(0.05)
            called.set()

        dal = MagicMock(enabled=True)
        dal.record_usage_event = slow_record
        state = _make_state(dal=dal)
        record_error(state, RuntimeError("x"))
        # Caller returns immediately; the executor worker is still running.
        # Wait briefly for it to finish.
        assert called.wait(timeout=2.0), (
            "executor worker did not run record_usage_event in the background"
        )

    def test_dal_exception_does_not_propagate(self, monkeypatch):
        """If record_usage_event raises, the caller of _fire must not see it.
        In production the real ThreadPoolExecutor parks the exception on the
        returned Future (which we never await), so nothing bubbles. The
        inline stub mimics that swallow semantic for deterministic testing."""
        _patch_inline_thread(monkeypatch)

        dal = MagicMock(enabled=True)
        dal.record_usage_event.side_effect = RuntimeError("supabase down")
        state = _make_state(dal=dal)

        # Should NOT raise — fire-and-forget contract.
        record_error(state, RuntimeError("x"))

    def test_executor_shutdown_is_silently_handled(self, monkeypatch):
        """If the executor was shut down (process exiting), submit raises
        RuntimeError. _fire catches that and accepts the loss — same fate
        as in-flight rows on the previous daemon-thread shape during
        process shutdown."""
        import holmes.core.usage_recorder as mod

        class _ShutdownExecutor:
            def submit(self, fn, *args, **kwargs):
                raise RuntimeError("cannot schedule new futures after shutdown")

        monkeypatch.setattr(mod, "_RECORDER_EXECUTOR", _ShutdownExecutor())

        dal = MagicMock(enabled=True)
        state = _make_state(dal=dal)

        # Should NOT raise — _fire's `except RuntimeError` accepts the loss.
        record_error(state, RuntimeError("x"))
