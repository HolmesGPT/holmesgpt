"""The metric definitions, checked against hand-computed values."""

import pytest

from holmes.core.investigation_path.metrics import (
    CaseOutcome,
    brier_score,
    expected_calibration_error,
    score_cases,
)


def case(
    incident_id="HOLD-1",
    abstained=False,
    missing=None,
    suggested=None,
    confidence=None,
    latency_ms=1.0,
    abstain_reason=None,
):
    return CaseOutcome(
        incident_id=incident_id,
        abstained=abstained,
        abstain_reason=abstain_reason,
        missing_weights=missing or {},
        suggested=suggested or [],
        suggestion_confidence=confidence or {},
        latency_ms=latency_ms,
    )


class TestCaseClassification:
    def test_suggestions_split_into_correct_and_wrong(self):
        outcome = case(missing={"a": 1.0}, suggested=["a", "b"])
        assert outcome.true_positives == ["a"]
        assert outcome.false_positives == ["b"]


class TestRecall:
    def test_recovering_every_missing_check_gives_full_recall(self):
        metrics = score_cases([case(missing={"a": 1.0, "b": 0.5}, suggested=["a", "b"])])
        assert metrics.weighted_path_recall == 1.0

    def test_recall_is_weighted_by_importance(self):
        # Finding the 0.9 check out of 0.9 + 0.1 is worth much more than the 0.1.
        metrics = score_cases([case(missing={"heavy": 0.9, "light": 0.1}, suggested=["heavy"])])
        assert metrics.weighted_path_recall == pytest.approx(0.9)

    def test_abstaining_costs_recall(self):
        metrics = score_cases(
            [
                case("A", missing={"a": 1.0}, suggested=["a"]),
                case("B", abstained=True, missing={"b": 1.0}, abstain_reason="no_candidates"),
            ]
        )
        assert metrics.weighted_path_recall == 0.5

    def test_recall_when_answering_ignores_abstentions(self):
        metrics = score_cases(
            [
                case("A", missing={"a": 1.0}, suggested=["a"]),
                case("B", abstained=True, missing={"b": 1.0}, abstain_reason="no_candidates"),
            ]
        )
        assert metrics.weighted_path_recall_when_answering == 1.0

    def test_a_case_with_nothing_missing_does_not_break_recall(self):
        metrics = score_cases([case(missing={}, suggested=[])])
        assert metrics.weighted_path_recall == 0.0


class TestPrecisionAndBurden:
    def test_precision_counts_wrong_suggestions(self):
        metrics = score_cases([case(missing={"a": 1.0}, suggested=["a", "b", "c"])])
        assert metrics.suggestion_precision == pytest.approx(1 / 3)

    def test_a_trivial_correct_check_cannot_pay_for_a_wrong_one(self):
        # One true positive worth 0.1 against one false positive charged 1.0.
        metrics = score_cases([case(missing={"a": 0.1}, suggested=["a", "wrong"])])
        assert metrics.suggestion_precision == 0.5
        assert metrics.weighted_suggestion_precision == pytest.approx(0.1 / 1.1)

    def test_false_positive_burden_is_per_answered_case(self):
        metrics = score_cases(
            [
                case("A", missing={"a": 1.0}, suggested=["a", "x", "y"]),
                case("B", missing={"b": 1.0}, suggested=["b"]),
            ]
        )
        assert metrics.false_positive_burden == 1.0

    def test_precision_hides_burden_which_is_why_both_are_reported(self):
        few = score_cases([case(missing={"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}, suggested=["a", "b", "c", "d", "x"])])
        many = score_cases(
            [
                case(
                    missing={str(i): 1.0 for i in range(20)},
                    suggested=[str(i) for i in range(20)] + ["x", "y", "z", "w", "v"],
                )
            ]
        )
        assert few.suggestion_precision == pytest.approx(0.8)
        assert many.suggestion_precision == pytest.approx(0.8)
        assert many.false_positive_burden > few.false_positive_burden


class TestAbstention:
    def test_abstention_rate_and_reasons_are_reported(self):
        metrics = score_cases(
            [
                case("A", missing={"a": 1.0}, suggested=["a"]),
                case("B", abstained=True, abstain_reason="no_candidates"),
                case("C", abstained=True, abstain_reason="no_candidates"),
                case("D", abstained=True, abstain_reason="insufficient_support"),
            ]
        )
        assert metrics.abstention_rate == 0.75
        assert metrics.abstain_reasons == {"no_candidates": 2, "insufficient_support": 1}

    def test_abstained_suggestions_are_excluded_from_precision(self):
        metrics = score_cases(
            [case("A", abstained=True, missing={"a": 1.0}, abstain_reason="no_candidates")]
        )
        assert metrics.suggestion_precision == 0.0
        assert metrics.answered_cases == 0


class TestCalibration:
    def test_perfect_calibration_scores_near_zero(self):
        # Everything stated at ~0.95 and everything correct.
        assert expected_calibration_error([0.95] * 10, [True] * 10) == pytest.approx(0.05)

    def test_confident_and_wrong_scores_high(self):
        assert expected_calibration_error([0.95] * 10, [False] * 10) == pytest.approx(0.95)

    def test_underconfidence_is_penalised_too(self):
        # Always right but only ever claims 0.25 confidence.
        assert expected_calibration_error([0.25] * 8, [True] * 8) == pytest.approx(0.75)

    def test_empty_input_is_zero(self):
        assert expected_calibration_error([], []) == 0.0
        assert brier_score([], []) == 0.0

    def test_brier_rewards_confident_correctness(self):
        assert brier_score([1.0, 1.0], [True, True]) == 0.0
        assert brier_score([0.0, 0.0], [True, True]) == 1.0


class TestAggregation:
    def test_scoring_an_empty_holdout_is_an_error(self):
        with pytest.raises(ValueError):
            score_cases([])

    def test_latency_percentiles_are_reported(self):
        cases = [case(str(i), missing={"a": 1.0}, suggested=["a"], latency_ms=float(i)) for i in range(1, 11)]
        metrics = score_cases(cases)
        assert metrics.latency_p50_ms == pytest.approx(5.0)
        assert metrics.latency_p95_ms == pytest.approx(10.0)

    def test_latency_percentiles_of_a_single_case(self):
        metrics = score_cases([case(missing={"a": 1.0}, suggested=["a"], latency_ms=3.0)])
        assert metrics.latency_p50_ms == 3.0
        assert metrics.latency_p95_ms == 3.0

    def test_llm_calls_are_tracked_and_expected_to_be_zero(self):
        metrics = score_cases([case(missing={"a": 1.0}, suggested=["a"])])
        assert metrics.llm_calls == 0

    def test_report_renders_every_metric(self):
        rendered = score_cases(
            [
                case("A", missing={"a": 1.0}, suggested=["a"], confidence={"a": 0.4}),
                case("B", abstained=True, abstain_reason="no_candidates"),
            ]
        ).render()
        for label in [
            "weighted path recall",
            "suggestion precision",
            "false positives per answer",
            "expected calibration error",
            "brier score",
            "abstain reasons",
        ]:
            assert label in rendered
