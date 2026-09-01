from types import SimpleNamespace

import pytest

from holmes.common.env_vars import LOOP_DETECTION_MAX_NUDGES
from holmes.core.loop_detection import (
    FORCE_ANSWER_SIGNATURE,
    KIND_ALTERNATING_TOOL_CALLS,
    KIND_DEGENERATE_OUTPUT,
    KIND_NARRATION_LOOP,
    KIND_REPEATED_ERRORS,
    KIND_REPEATED_TOOL_CALLS,
    LoopDetector,
    LoopSignal,
    _window_size,
    build_loop_breaker_message,
    degenerate_repetition_ratio,
    signature_for_tool_calls,
    summarize_degenerate_text,
)


def tool_call(name: str, arguments: str):
    """Minimal stand-in for litellm's ChatCompletionMessageToolCall."""
    return SimpleNamespace(
        id=f"call_{name}", function=SimpleNamespace(name=name, arguments=arguments)
    )


class TestSignature:
    def test_identical_calls_share_a_signature(self):
        a = [tool_call("kubectl_get", '{"name": "pod-1", "ns": "default"}')]
        b = [tool_call("kubectl_get", '{"ns": "default", "name": "pod-1"}')]
        assert signature_for_tool_calls(a) == signature_for_tool_calls(b)

    def test_different_params_differ(self):
        a = [tool_call("kubectl_get", '{"name": "pod-1"}')]
        b = [tool_call("kubectl_get", '{"name": "pod-2"}')]
        assert signature_for_tool_calls(a) != signature_for_tool_calls(b)

    def test_parallel_batch_order_is_ignored(self):
        a = [tool_call("t1", "{}"), tool_call("t2", "{}")]
        b = [tool_call("t2", "{}"), tool_call("t1", "{}")]
        assert signature_for_tool_calls(a) == signature_for_tool_calls(b)

    def test_no_tool_calls_is_empty(self):
        assert signature_for_tool_calls([]) == ""

    def test_unparseable_arguments_do_not_raise(self):
        assert signature_for_tool_calls([tool_call("t1", "not json{{")]) != ""


class TestRepeatedToolCalls:
    def test_three_identical_turns_trip_the_detector(self):
        detector = LoopDetector()
        calls = [tool_call("fetch_api", '{"url": "/v1/status"}')]

        assert detector.record_turn(calls, "checking", None) is None
        assert detector.record_turn(calls, "checking", None) is None
        signal = detector.record_turn(calls, "checking", None)

        assert signal is not None
        assert signal.kind == KIND_REPEATED_TOOL_CALLS

    def test_varying_the_tool_call_does_not_trip_it(self):
        detector = LoopDetector()
        for i in range(6):
            signal = detector.record_turn(
                [tool_call("fetch_api", '{"url": "/v1/%s"}' % i)], None, None
            )
            assert signal is None


class TestAlternatingToolCalls:
    def test_ping_pong_trips_after_three_cycles(self):
        detector = LoopDetector()
        a = [tool_call("list_pods", "{}")]
        b = [tool_call("describe_pod", '{"name": "p"}')]

        signals = [detector.record_turn(c, None, None) for c in (a, b, a, b, a, b)]

        assert all(s is None for s in signals[:-1])
        assert signals[-1] is not None
        assert signals[-1].kind == KIND_ALTERNATING_TOOL_CALLS


class TestRepeatedErrors:
    def test_three_all_error_turns_trip_the_detector(self):
        detector = LoopDetector()
        kinds = []
        for i in range(3):
            signal = detector.record_turn(
                [tool_call("query", '{"q": "%s"}' % i)],
                None,
                None,
                tool_results_all_errored=True,
            )
            if signal:
                kinds.append(signal.kind)
        assert kinds == [KIND_REPEATED_ERRORS]

    def test_a_successful_turn_resets_the_streak(self):
        detector = LoopDetector()
        for i, errored in enumerate([True, True, False, True]):
            signal = detector.record_turn(
                [tool_call("query", '{"q": "%s"}' % i)],
                None,
                None,
                tool_results_all_errored=errored,
            )
            assert signal is None


class TestNarrationLoop:
    def test_restating_the_same_intent_trips_the_detector(self):
        detector = LoopDetector()
        narrations = [
            "Let me examine the payments API to understand the failure",
            "Let me examine the payments API to understand the problem",
            "Let me examine the payments API to understand the failures",
        ]
        signals = [
            detector.record_turn([tool_call("t", '{"i": %d}' % i)], text, None)
            for i, text in enumerate(narrations)
        ]

        assert signals[-1] is not None
        assert signals[-1].kind == KIND_NARRATION_LOOP

    def test_genuinely_different_narration_is_fine(self):
        detector = LoopDetector()
        narrations = [
            "Checking the pod logs for the crash reason",
            "The logs point at a failed database migration, looking at the job",
            "The migration job ran out of memory, checking the resource limits",
        ]
        for i, text in enumerate(narrations):
            assert (
                detector.record_turn([tool_call("t", '{"i": %d}' % i)], text, None)
                is None
            )

    def test_short_narration_is_ignored(self):
        detector = LoopDetector()
        for i in range(4):
            assert (
                detector.record_turn([tool_call("t", '{"i": %d}' % i)], "ok", None)
                is None
            )


class TestDegenerateOutput:
    def test_ratio_is_low_for_normal_prose(self):
        text = " ".join(
            f"step {i} inspects a different part of the cluster state"
            for i in range(40)
        )
        assert degenerate_repetition_ratio(text) < 0.5

    def test_ratio_is_high_for_collapsed_text(self):
        text = "Let me look at the API and check what it returns. " * 40
        assert degenerate_repetition_ratio(text) > 0.9

    def test_short_text_is_never_degenerate(self):
        assert degenerate_repetition_ratio("Let me look at the API. " * 3) == 0.0

    def test_none_is_safe(self):
        assert degenerate_repetition_ratio(None) == 0.0

    def test_detector_flags_degenerate_reasoning(self):
        detector = LoopDetector()
        signal = detector.record_turn(
            [tool_call("t", "{}")],
            None,
            "I will now search the API. " * 60,
        )
        assert signal is not None
        assert signal.kind == KIND_DEGENERATE_OUTPUT

    def test_summarize_keeps_a_prefix_and_marks_the_trim(self):
        text = "alpha beta gamma " * 500
        trimmed = summarize_degenerate_text(text, max_words=50)
        assert trimmed.startswith("alpha beta gamma")
        assert len(trimmed.split()) < 80
        assert "truncated" in trimmed

    def test_summarize_leaves_short_text_alone(self):
        assert summarize_degenerate_text("short text") == "short text"


class TestEscalation:
    def test_first_detections_nudge_then_force_an_answer(self):
        detector = LoopDetector()
        calls = [tool_call("fetch_api", '{"url": "/v1/status"}')]

        collected = []
        for _ in range(12):
            signal = detector.record_turn(calls, None, None)
            if signal:
                collected.append(signal)

        assert len(collected) >= 3
        assert collected[0].should_force_answer is False
        assert collected[1].should_force_answer is False
        assert collected[2].should_force_answer is True

    def test_nudge_message_is_a_user_message_with_alternatives(self):
        detector = LoopDetector()
        calls = [tool_call("fetch_api", "{}")]
        signal = None
        while signal is None:
            signal = detector.record_turn(calls, None, None)

        msg = build_loop_breaker_message(signal)
        assert msg["role"] == "user"
        assert "Do not repeat" in msg["content"]
        assert "final answer" in msg["content"]

    def test_forced_message_tells_the_model_tools_are_gone(self):
        detector = LoopDetector()
        calls = [tool_call("fetch_api", "{}")]
        signals = [
            s for s in (detector.record_turn(calls, None, None) for _ in range(12)) if s
        ]
        forced = next(s for s in signals if s.should_force_answer)

        content = build_loop_breaker_message(forced)["content"]
        assert "STOP." in content
        assert "no further tool calls" in content


class TestWindowSizing:
    """The sliding window must never be smaller than a configured threshold."""

    def test_a_threshold_above_the_window_still_fires(self, monkeypatch):
        # Window of 4 but a repeat threshold of 6: without sizing the window to
        # the widest threshold, the history is trimmed before the check can see
        # 6 turns and the check silently never fires.
        monkeypatch.setattr("holmes.core.loop_detection.LOOP_DETECTION_WINDOW", 4)
        monkeypatch.setattr(
            "holmes.core.loop_detection.LOOP_DETECTION_REPEAT_THRESHOLD", 6
        )
        detector = LoopDetector()
        calls = [tool_call("fetch_api", "{}")]

        signals = [detector.record_turn(calls, None, None) for _ in range(6)]

        assert all(s is None for s in signals[:5])
        assert signals[5] is not None
        assert signals[5].kind == KIND_REPEATED_TOOL_CALLS

    def test_error_streak_above_the_window_still_fires(self, monkeypatch):
        monkeypatch.setattr("holmes.core.loop_detection.LOOP_DETECTION_WINDOW", 2)
        monkeypatch.setattr("holmes.core.loop_detection.LOOP_DETECTION_ERROR_STREAK", 5)
        detector = LoopDetector()

        signals = [
            detector.record_turn(
                [tool_call("query", '{"q": %d}' % i)],
                None,
                None,
                tool_results_all_errored=True,
            )
            for i in range(5)
        ]

        assert signals[-1] is not None
        assert signals[-1].kind == KIND_REPEATED_ERRORS


class TestEscalationBudgetIsPerRun:
    """reset() clears the turn window but not the escalation budget."""

    def test_a_second_independent_loop_still_escalates(self):
        detector = LoopDetector()
        a = [tool_call("fetch_api", "{}")]
        b = [tool_call("list_pods", "{}")]

        # First loop: detected, nudged, then the window is cleared as the agent
        # loop does after a nudge.
        for _ in range(3):
            first = detector.record_turn(a, None, None)
        assert first is not None and first.should_force_answer is False
        detector.reset()

        # A different loop later in the same run must not restart the budget.
        for _ in range(3):
            second = detector.record_turn(b, None, None)
        assert second is not None
        assert second.nudge_count == 1, "escalation budget must carry across loops"
        detector.reset()

        for _ in range(3):
            third = detector.record_turn(a, None, None)
        assert third is not None
        assert (
            third.should_force_answer is True
        ), "a run that keeps looping in new shapes must still be forced to answer"


def assistant(tool_name: str, args: str, text: str = "working on it") -> dict:
    """An assistant message as it appears in a transcript."""
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {"name": tool_name, "arguments": args},
            }
        ],
    }


class TestSeedFromMessages:
    """State is rebuilt from the transcript, so pauses cannot reset it."""

    def test_a_loop_spanning_a_pause_still_trips(self):
        # Two looping turns happened before the pause. After the pause a fresh
        # detector is built from the transcript; the third identical turn must
        # complete the loop rather than starting the count over.
        transcript = [
            {"role": "user", "content": "check the API"},
            assistant("fetch_api", '{"url": "/v1"}'),
            assistant("fetch_api", '{"url": "/v1"}'),
        ]
        detector = LoopDetector()
        detector.seed_from_messages(transcript)

        signal = detector.record_turn(
            [tool_call("fetch_api", '{"url": "/v1"}')], "working on it", None
        )

        assert signal is not None
        assert signal.kind == KIND_REPEATED_TOOL_CALLS

    def test_without_seeding_the_same_loop_escapes(self):
        # Pins the bug this fixes: a fresh, unseeded detector sees only one turn.
        detector = LoopDetector()
        assert (
            detector.record_turn(
                [tool_call("fetch_api", '{"url": "/v1"}')], "working on it", None
            )
            is None
        )

    def test_nudges_already_issued_are_recovered(self):
        signal = LoopSignal(kind=KIND_REPEATED_TOOL_CALLS, detail="d", nudge_count=0)
        nudge = build_loop_breaker_message(signal)

        detector = LoopDetector()
        detector.seed_from_messages(
            [
                {"role": "user", "content": "check the API"},
                assistant("fetch_api", "{}"),
                nudge,
            ]
        )

        assert detector.nudge_count == 1

    def test_a_nudge_in_the_transcript_also_clears_the_window(self):
        """The runtime calls reset() after nudging; the replay must match."""
        nudge = build_loop_breaker_message(
            LoopSignal(kind=KIND_REPEATED_TOOL_CALLS, detail="d", nudge_count=0)
        )
        detector = LoopDetector()
        detector.seed_from_messages(
            [
                {"role": "user", "content": "go"},
                assistant("fetch_api", "{}"),
                assistant("fetch_api", "{}"),
                nudge,
                assistant("fetch_api", "{}"),
            ]
        )

        # Only the post-nudge turn survives, so two more are needed to trip.
        assert (
            detector.record_turn([tool_call("fetch_api", "{}")], "working on it", None)
            is None
        )

    def test_forced_answer_message_counts_but_keeps_the_window(self):
        forced = build_loop_breaker_message(
            LoopSignal(
                kind=KIND_REPEATED_TOOL_CALLS,
                detail="d",
                nudge_count=LOOP_DETECTION_MAX_NUDGES,
            )
        )
        assert FORCE_ANSWER_SIGNATURE in forced["content"]

        detector = LoopDetector()
        detector.seed_from_messages(
            [
                {"role": "user", "content": "go"},
                assistant("fetch_api", "{}"),
                assistant("fetch_api", "{}"),
                forced,
            ]
        )

        assert detector.nudge_count == 1
        # Window kept: the next identical turn completes the streak of three.
        signal = detector.record_turn(
            [tool_call("fetch_api", "{}")], "working on it", None
        )
        assert signal is not None

    def test_a_new_user_question_starts_fresh(self):
        """A follow-up question must not inherit the previous run's state."""
        nudge = build_loop_breaker_message(
            LoopSignal(kind=KIND_REPEATED_TOOL_CALLS, detail="d", nudge_count=0)
        )
        detector = LoopDetector()
        detector.seed_from_messages(
            [
                {"role": "user", "content": "first question"},
                assistant("fetch_api", "{}"),
                assistant("fetch_api", "{}"),
                nudge,
                {"role": "user", "content": "a completely new question"},
            ]
        )

        assert detector.nudge_count == 0
        assert (
            detector.record_turn([tool_call("fetch_api", "{}")], "working on it", None)
            is None
        )

    def test_content_parts_format_is_handled(self):
        """cache_control turns string content into a list of text parts."""
        nudge = build_loop_breaker_message(
            LoopSignal(kind=KIND_REPEATED_TOOL_CALLS, detail="d", nudge_count=0)
        )
        nudge_as_parts = {
            "role": "user",
            "content": [{"type": "text", "text": nudge["content"]}],
        }
        detector = LoopDetector()
        detector.seed_from_messages([nudge_as_parts])
        assert detector.nudge_count == 1

    def test_malformed_transcripts_do_not_raise(self):
        for bad in ([], [{}], [{"role": "assistant"}], [{"role": "tool"}], None):
            detector = LoopDetector()
            detector.seed_from_messages(bad)
            assert detector.nudge_count == 0

    def test_seeding_respects_the_window(self):
        """A long transcript is trimmed to the same bound record_turn uses."""
        detector = LoopDetector()
        detector.seed_from_messages(
            [{"role": "user", "content": "go"}]
            + [assistant("t", '{"i": %d}' % i) for i in range(50)]
        )
        assert len(detector._turns) == _window_size()


class TestDisabled:
    def test_detector_is_inert_when_disabled(self, monkeypatch):
        monkeypatch.setattr("holmes.core.loop_detection.LOOP_DETECTION_ENABLED", False)
        detector = LoopDetector()
        calls = [tool_call("fetch_api", "{}")]
        for _ in range(10):
            assert detector.record_turn(calls, None, None) is None


class TestRobustness:
    @pytest.mark.parametrize("bad", [None, [], [SimpleNamespace()], [{}]])
    def test_malformed_tool_calls_do_not_raise(self, bad):
        LoopDetector().record_turn(bad, None, None)
