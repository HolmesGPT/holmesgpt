"""Skill-suggestion check failures in the eval reports (ROB-1715).

The check still fails the run, but reports show it apart from a wrong answer
and keep the judge's own verdict.
"""

from tests.generate_eval_report import (
    generate_eval_dashboard_heatmap,
    generate_skill_check_section,
    generate_summary_table,
    get_test_skill_check_failure,
)
from tests.llm.utils.braintrust_history import _extract_metrics
from tests.llm.utils.reporting.github_reporter import generate_markdown_report
from tests.llm.utils.reporting.skill_check import (
    SkillCheckFailure,
    classify_skill_check_failure,
    failure_message,
)
from tests.llm.utils.test_results import TestStatus

OLD_ASSERTION = (
    "AssertionError: Test 51_logs_summarize_errors expected NO skill suggestions "
    "but the LLM emitted 1. This usually means the SuggestSkills tool/prompt is "
    "being too eager. Suggestions:\n[{'title': 'Workload naming convention'}]"
)
SUGGESTIONS = [{"title": "Workload naming convention in this cluster"}]


def _test(case, model, outcome, props=None, crash=None, longrepr=""):
    user_props = [{"model": model}, {"clean_test_case_id": case}]
    user_props += [{k: v} for k, v in (props or {}).items()]
    call = {"longrepr": longrepr}
    if crash:
        call["crash"] = {"message": crash}
    return {
        "nodeid": f"tests/llm/test_ask_holmes.py::test_ask_holmes[{case}-{model}-default]",
        "outcome": outcome,
        "user_properties": user_props,
        "call": call,
    }


def _new_format(case, model, judge_score):
    return _test(
        case,
        model,
        "failed",
        {
            "actual_correctness_score": judge_score,
            "fail_reason": "unexpected_skill_suggestion",
            "suggested_skill_titles": [s["title"] for s in SUGGESTIONS],
            "suggested_memories": SUGGESTIONS,
        },
    )


def _old_format(case, model):
    # Reports from before fail_reason: judge score overwritten to 0, only the
    # assertion text says why.
    return _test(
        case,
        model,
        "failed",
        {"actual_correctness_score": 0, "suggested_memories": SUGGESTIONS},
        crash=OLD_ASSERTION,
    )


def test_classify_from_fail_reason():
    assert (
        classify_skill_check_failure({"fail_reason": "missing_skill_update"})
        is SkillCheckFailure.MISSING_UPDATE
    )


def test_classify_from_old_assertion_text():
    assert (
        classify_skill_check_failure({}, OLD_ASSERTION)
        is SkillCheckFailure.UNEXPECTED_SUGGESTION
    )
    assert (
        classify_skill_check_failure(
            {}, "AssertionError: Test 7 expected at least one skill suggestion but"
        )
        is SkillCheckFailure.MISSING_SUGGESTION
    )


def test_source_lines_in_traceback_are_not_a_skill_check_failure():
    # pytest's long traceback prints the test source, which contains the
    # assertion phrases even when a different assertion fired.
    longrepr = (
        "        raise AssertionError(\n"
        '            f"Test {test_case.id} expected NO skill suggestions but the "\n'
        "E   AssertionError: Test 254 failed (score: 0)\n"
    )
    assert (
        failure_message({"longrepr": longrepr})
        == "AssertionError: Test 254 failed (score: 0)"
    )
    assert (
        get_test_skill_check_failure(_test("254", "m", "failed", longrepr=longrepr))
        is None
    )


def test_passed_run_is_never_a_skill_check_failure():
    assert (
        get_test_skill_check_failure(_test("x", "m", "passed", crash=OLD_ASSERTION))
        is None
    )


def test_section_shows_judge_verdict_and_skill_title():
    results = {
        "tests": [
            _new_format("51_logs", "kimi", 1),
            _new_format("24_pvc", "deepseek", 0),
            _old_format("227_count", "glm"),
            _test("09_crash", "kimi", "failed", {"actual_correctness_score": 0}),
        ]
    }
    section = generate_skill_check_section(results)
    title = "Workload naming convention in this cluster"
    assert "## Skill-Suggestion Check Failures" in section
    assert (
        f"| 🧩 | 51_logs | kimi | suggested a skill, expected none | correct | {title} |"
        in section
    )
    assert "| 24_pvc | deepseek | suggested a skill, expected none | wrong |" in section
    # Old report: the title comes from suggested_memories, the verdict is unknown.
    assert (
        f"| 227_count | glm | suggested a skill, expected none | not recorded | {title} |"
        in section
    )
    assert '"not recorded"' in section
    assert "09_crash" not in section


def test_section_empty_without_skill_check_failures():
    assert generate_skill_check_section({"tests": [_test("a", "m", "failed")]}) == ""


def test_skill_check_failures_still_count_as_failures(monkeypatch):
    # Cells are links only when a Braintrust key is configured.
    monkeypatch.setattr("tests.generate_eval_report.BRAINTRUST_API_KEY", "test-key")
    tests = [_test(f"{i}_ok", "kimi", "passed") for i in range(3)]
    tests.append(_new_format("51_logs", "kimi", 1))
    results = {"tests": tests}
    assert "| kimi | 3 | 1 | 0 | 4 | 🟡 75% (3/4) |" in generate_summary_table(results)
    heatmap = generate_eval_dashboard_heatmap(results)
    assert "[🔴🧩](" in heatmap
    assert "🧩 Failed the skill-suggestion check" in heatmap


def test_github_status_marks_skill_check_failure():
    result = {
        "status": "failed",
        "actual_correctness_score": 1,
        "fail_reason": "unexpected_skill_suggestion",
    }
    status = TestStatus(result)
    assert status.passed is False
    assert status.is_regression is True
    assert status.markdown_symbol == ":x::jigsaw:"
    assert status.short_status == "FAIL (skill check)"


def test_github_status_ignores_traceback_text_without_fail_reason():
    status = TestStatus(
        {
            "status": "failed",
            "actual_correctness_score": 0,
            "error_message": OLD_ASSERTION,
        }
    )
    assert status.skill_check_failure is None
    assert status.markdown_symbol == ":x:"


def test_github_report_lists_skill_check_regressions():
    row = {
        "test_type": "ask",
        "test_case_name": "51_logs_summarize_errors",
        "status": "failed",
        "actual_correctness_score": 1.0,
        "expected_correctness_score": 1.0,
        "fail_reason": "unexpected_skill_suggestion",
    }
    markdown, _, regressions = generate_markdown_report([row], include_historical=False)
    assert regressions == 1
    assert ":jigsaw: 1 of the regressions failed the skill-suggestion check" in markdown
    assert "| :x::jigsaw:" in markdown


def test_benchmark_baseline_counts_skill_check_failure_as_failed():
    span = {
        "metadata": {"test_id": "51_logs", "model": "kimi"},
        "scores": {"correctness": 1, "skill_check": 0},
    }
    metrics = _extract_metrics(span)
    assert metrics is not None and metrics.passed is False
    span["scores"]["skill_check"] = 1
    assert _extract_metrics(span).passed is True
