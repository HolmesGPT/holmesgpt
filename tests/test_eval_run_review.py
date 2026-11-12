"""Analysis behind the review-eval-run skill (ROB-1720).

The pytest report is the source of truth: runs that never reached the model
stay out of the clean pass rate, and one-iteration runs are judged by how many
models fail a test.
"""

from tests.llm.utils.reporting.eval_run_review import (
    NotExecuted,
    compare_with_baseline,
    iteration_count,
    missing_from_braintrust,
    parse_markdown_report,
    parse_results,
    suspect_tests,
)
from tests.llm.utils.reporting.skill_check import SkillCheckFailure

UPSTREAM = (
    "litellm.BadRequestError: OpenrouterException - Upstream error from DeepInfra: "
    "unexpected end of data"
)
OLD_SKILL_ASSERTION = (
    "AssertionError: Test 51_logs_summarize_errors expected NO skill suggestions "
    "but the LLM emitted 1."
)


def _test(case, model, outcome="passed", suffix="", crash=None, **props):
    user_props = [
        {"actual": "Test not executed"},
        {"model": model},
        {"clean_test_case_id": case},
    ]
    user_props += [{k: v} for k, v in props.items()]
    return {
        "nodeid": f"tests/llm/test_ask_holmes.py::test_ask_holmes[{case}-{model}-default{suffix}]",
        "outcome": outcome,
        "user_properties": user_props,
        "call": {"crash": {"message": crash}} if crash else {"longrepr": ""},
    }


def _answered(case, model, outcome="passed", cost=0.1, **props):
    return _test(case, model, outcome, actual="answer", cost=cost, **props)


def _report(*tests):
    return {"tests": list(tests)}


def test_parse_results_classifies_every_kind_of_run():
    runs = parse_results(
        _report(
            _answered("01_ok", "m1", braintrust_root_span_id="root-1"),
            _test("02_provider", "m1", "failed", error_type="BadRequestError", error_message=UPSTREAM),
            _test(
                "03_timeout",
                "m1",
                "failed",
                error_type="Timeout",
                error_message="litellm.Timeout: Connection timed out",
            ),
            _answered("04_skill", "m1", "failed", crash=OLD_SKILL_ASSERTION),
            _answered(
                "05_skill_new",
                "m1",
                "failed",
                fail_reason="missing_skill_suggestion",
                actual_correctness_score=1,
            ),
            _test("06_crash", "m1", "failed", error_type="KeyError", error_message="'x'"),
            _test("07_setup", "m1", "failed", is_setup_failure=True),
            _test("08_skipped", "m1", "skipped"),
            _answered("09_wrong", "m1", "failed"),
            _test("10_deselected", "m1", "deselected"),
        )
    )
    by_test = {r.test: r for r in runs}

    assert "10_deselected" not in by_test
    assert by_test["01_ok"].passed and by_test["01_ok"].root_span_id == "root-1"
    assert by_test["01_ok"].cost == 0.1
    assert by_test["02_provider"].not_executed is NotExecuted.PROVIDER
    assert by_test["02_provider"].error.startswith("BadRequestError: litellm.")
    assert by_test["03_timeout"].not_executed is NotExecuted.TIMEOUT
    assert by_test["04_skill"].skill_check is SkillCheckFailure.UNEXPECTED_SUGGESTION
    assert by_test["04_skill"].judge_correct is None  # old reports overwrote it
    assert by_test["04_skill"].failed
    assert by_test["05_skill_new"].skill_check is SkillCheckFailure.MISSING_SUGGESTION
    assert by_test["05_skill_new"].judge_correct is True
    assert by_test["06_crash"].not_executed is NotExecuted.CRASH
    assert by_test["07_setup"].not_executed is NotExecuted.SETUP
    assert by_test["08_skipped"].not_executed is NotExecuted.SKIPPED
    assert by_test["09_wrong"].failed and by_test["09_wrong"].not_executed is None


def test_iterations_keep_collection_order():
    runs = parse_results(
        _report(
            _answered("t", "m", "failed", suffix="10"),
            _answered("t", "m", suffix="0"),
            _answered("t", "m", "failed", suffix="1"),
        )
    )
    assert iteration_count(runs) == 3
    assert [r.passed for r in runs] == [True, False, False]


def test_single_iteration_flags_tests_failing_for_half_the_models_that_ran_them():
    runs = parse_results(
        _report(
            # half of the executed models fail; the provider error doesn't count
            _answered("broad", "m1", "failed"),
            _answered("broad", "m2"),
            _test("broad", "m3", "failed", error_type="BadRequestError", error_message=UPSTREAM),
            # one of three
            _answered("narrow", "m1", "failed"),
            _answered("narrow", "m2"),
            _answered("narrow", "m3"),
            # only one model ran it
            _answered("lonely", "m1", "failed"),
            _test("lonely", "m2", "failed", error_type="BadRequestError", error_message=UPSTREAM),
        )
    )
    suspects = suspect_tests(runs)
    assert [s.test for s in suspects] == ["broad"]
    assert suspects[0].pattern == "fails for 1/2 models that ran it"
    assert suspects[0].per_model == {"m1": (0, 1)}


def test_multiple_iterations_keep_the_iteration_patterns():
    runs = parse_results(
        _report(
            _answered("decay", "m1", suffix="0"),
            _answered("decay", "m1", "failed", suffix="1"),
            _answered("decay", "m2", suffix="0"),
            _answered("decay", "m2", "failed", suffix="1"),
            _answered("stable", "m1", suffix="0"),
            _answered("stable", "m1", suffix="1"),
        )
    )
    suspects = suspect_tests(runs)
    assert [(s.test, s.pattern) for s in suspects] == [
        ("decay", "passes only on first iteration (state decay?)")
    ]


def test_baseline_comparison_rates_costs_and_regressions():
    current = parse_results(
        _report(
            _answered("a", "m1", cost=1.0),
            _answered("b", "m1", "failed", cost=1.0, fail_reason="unexpected_skill_suggestion"),
            _answered("eval_bug", "m1", "failed", cost=1.0),
            _test("c", "m1", "failed", error_type="BadRequestError", error_message=UPSTREAM),
            _answered("a", "new_model"),
            _answered("a", "steady", cost=1.5),
        )
    )
    baseline = parse_results(
        _report(
            _answered("a", "m1", cost=0.5),
            _answered("b", "m1", cost=0.5),
            _answered("eval_bug", "m1", cost=0.5),
            _answered("c", "m1", cost=0.5),
            _answered("a", "steady", cost=1.0),
        )
    )
    by_model = {c.model: c for c in compare_with_baseline(current, baseline, {"eval_bug"})}

    m1 = by_model["m1"]
    assert (m1.current.passed, m1.current.runs) == (1, 4)
    assert (m1.current.clean_passed, m1.current.clean_runs) == (1, 2)
    assert m1.current.cost == 3.0 and m1.baseline and m1.baseline.cost == 2.0
    assert m1.cost_spike is False  # exactly 1.5x
    # c never executed now, so it is not a regression
    assert [(r.test, r.now, r.excluded, r.skill_check) for r in m1.regressions] == [
        ("b", "0/1", False, True),
        ("eval_bug", "0/1", True, False),
    ]
    assert by_model["new_model"].baseline is None
    assert by_model["new_model"].cost_spike is False
    assert by_model["steady"].cost_spike is False


def test_cost_spike_above_one_and_a_half_times_baseline():
    current = parse_results(_report(_answered("a", "m", cost=1.79)))
    baseline = parse_results(_report(_answered("a", "m", cost=0.47)))
    (comparison,) = compare_with_baseline(current, baseline, set())
    assert comparison.cost_spike
    assert round(comparison.cost_ratio or 0, 1) == 3.8


def test_missing_from_braintrust():
    runs = parse_results(
        _report(
            _answered("a", "m", braintrust_root_span_id="r1"),
            _answered("b", "m", braintrust_root_span_id="r2"),
        )
    )
    assert [r.test for r in missing_from_braintrust(runs, {"r1"})] == ["b"]


MARKDOWN_REPORT = """# ⚡ October 18, 2026

## Model Cost Comparison

| Model | Tests | Avg Cost | Min Cost | Max Cost | Total Cost |
|-------|-------|----------|----------|----------|------------|
| m1 | 3 | $0.16 | $0.00 | $0.47 | $0.47 |
| m2 | 2 | $0.10 | $0.05 | $0.15 | $0.20 |

## Detailed Raw Results

| Eval ID | m1 | m2 |
|---------|-------|-------|
| [a](https://x) [🔗](https://y) | [🟢 100% (1/1)](https://z) / ⏱️ 23.8s / 💰 $0.01 | [🔴 0% (0/1)](https://z) / ⏱️ 2.0s / 💰 $0.05 |
| [227_count[0]](https://x) [🔗](https://y) | [⏱️ 0% (0/1)](https://z) / ⏱️ 1261.9s | [🟢 100% (1/1)](https://z) / ⏱️ 9.0s / 💰 $0.15 |
| [b](https://x) [🔗](https://y) | [🔴 0% (0/1)](https://z) / ⏱️ 16.6s | N/A |

---
"""


def test_markdown_report_as_baseline():
    runs, total_costs = parse_markdown_report(MARKDOWN_REPORT)
    by_key = {(r.test, r.model): r for r in runs}

    assert total_costs == {"m1": 0.47, "m2": 0.20}
    assert len(runs) == 5
    assert by_key[("a", "m1")].passed and by_key[("a", "m1")].cost == 0.01
    assert by_key[("a", "m2")].failed
    assert by_key[("227_count[0]", "m1")].not_executed is NotExecuted.TIMEOUT
    # a failure with no cost never got a model response
    assert by_key[("b", "m1")].not_executed is NotExecuted.CRASH

    current = parse_results(_report(_answered("a", "m1", cost=1.0)))
    (comparison,) = compare_with_baseline(current, runs, set(), total_costs)
    assert comparison.baseline and comparison.baseline.cost == 0.47
    assert (comparison.baseline.clean_passed, comparison.baseline.clean_runs) == (1, 1)
    assert comparison.cost_spike
