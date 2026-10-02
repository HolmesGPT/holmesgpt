"""Unit tests for TestStatus pass/fail reporting logic."""

from tests.llm.utils.test_results import TestStatus


def _result(**overrides):
    base = {
        "actual_correctness_score": 1,
        "expected_correctness_score": 1,
        "status": "passed",
    }
    base.update(overrides)
    return base


def test_passed_when_judge_and_pytest_both_pass():
    assert TestStatus(_result()).passed is True


def test_failed_when_judge_rejects_answer():
    assert TestStatus(_result(actual_correctness_score=0, status="failed")).passed is False


def test_failed_when_pytest_failed_even_if_judge_scored_one():
    """The score is logged BEFORE assertions like memories_generated or
    max_tokens fire. When such a post-judge assertion fires, pytest marks
    the test as failed but the score in user_properties is still 1. The
    report's pass/fail must respect pytest's outcome too — otherwise the
    GitHub markdown shows a green check on a failing test."""
    result = _result(actual_correctness_score=1, status="failed")
    status = TestStatus(result)
    assert status.passed is False
    assert status.is_regression is True


def test_empty_status_is_not_a_failure():
    """Skipped tests and tests that never set status (legacy path) should
    not be artificially marked as failing."""
    assert TestStatus(_result(status="")).passed is True


def test_skipped_is_not_a_pass_or_a_regression():
    s = TestStatus(_result(status="skipped"))
    assert s.is_skipped is True
    assert s.is_regression is False
