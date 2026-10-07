import contextvars
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from holmes.core import request_counters
from holmes.core.usage_recorder import (
    record_error,
    record_from_llm_result,
    stream_with_usage_recording,
)
from holmes.utils.stream import StreamEvents, StreamMessage
from tests.core.test_usage_recorder import (
    _make_state,
    _patch_inline_thread,
    _state_arg,
    _stream,
    _terminal_data,
)


class TestRequestCounters:
    def test_stream_binds_counters_for_inner_generator_and_tool_threads(
        self, monkeypatch
    ):
        _patch_inline_thread(monkeypatch)
        state = _make_state(meta={"slack": {"slack_user_id": "U1"}})

        def inner():
            request_counters.increment("datadog_calls")
            yield StreamMessage(event=StreamEvents.TOOL_RESULT, data={})
            with ThreadPoolExecutor(max_workers=2) as pool:
                for _ in range(2):
                    pool.submit(
                        contextvars.copy_context().run,
                        request_counters.increment,
                        "datadog_calls",
                    ).result()
            request_counters.increment("datadog_wait_ms_total", 250)
            yield StreamMessage(
                event=StreamEvents.ANSWER_END, data=_terminal_data({})
            )

        list(stream_with_usage_recording(inner(), state))

        assert _state_arg(state).meta == {
            "slack": {"slack_user_id": "U1"},
            "datadog_calls": 3,
            "datadog_wait_ms_total": 250,
        }

    def test_counters_not_bound_between_stream_steps(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        stream = stream_with_usage_recording(
            _stream(StreamMessage(event=StreamEvents.ANSWER_END, data=_terminal_data({}))),
            state,
        )

        next(stream)
        request_counters.increment("datadog_calls")
        list(stream)

        assert "datadog_calls" not in _state_arg(state).meta

    def test_track_binds_counters_for_non_streaming_calls(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()
        llm_result = MagicMock(num_llm_calls=1, tool_calls=[], finish_reason="stop")
        llm_result.model_dump.return_value = {}

        with state.track():
            request_counters.increment("datadog_429s", 2)
        request_counters.increment("datadog_429s")
        record_from_llm_result(state, llm_result)

        assert _state_arg(state).meta == {"datadog_429s": 2}

    def test_no_counters_leaves_meta_untouched(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        original_meta = {"experiment_id": "x"}
        state = _make_state(meta=original_meta)

        record_error(state, RuntimeError("boom"))

        assert _state_arg(state).meta == {"experiment_id": "x"}

    def test_merge_does_not_mutate_caller_meta(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        original_meta = {"experiment_id": "x"}
        state = _make_state(meta=original_meta)
        with state.track():
            request_counters.increment("datadog_calls")

        record_error(state, RuntimeError("boom"))

        assert original_meta == {"experiment_id": "x"}
        assert _state_arg(state).meta == {"experiment_id": "x", "datadog_calls": 1}

    def test_exception_in_stream_still_records_counters(self, monkeypatch):
        _patch_inline_thread(monkeypatch)
        state = _make_state()

        def failing():
            request_counters.increment("datadog_calls")
            raise RuntimeError("tool crashed")
            yield  # pragma: no cover

        with pytest.raises(RuntimeError):
            list(stream_with_usage_recording(failing(), state))

        assert _state_arg(state).status == "error"
        assert _state_arg(state).meta == {"datadog_calls": 1}
