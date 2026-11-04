"""Provider errors and timeouts in the benchmark report (ROB-1714).

A run that dies on a provider error never produced an answer, so the report
must show it as ⚠️/⏱️ and leave it out of the model's success rate.
"""

import pytest

from tests.generate_eval_report import (
    generate_eval_dashboard_heatmap,
    generate_infra_errors_section,
    generate_latency_comparison_table,
    generate_model_by_tag_table,
    generate_summary_table,
    get_test_status_emoji,
)
from tests.llm.utils.reporting.infra_errors import InfraError, classify_infra_error
from tests.llm.utils.test_results import TestStatus

NOT_FOUND = (
    "litellm.NotFoundError: NotFoundError: OpenrouterException - Publisher model "
    "`projects/1/locations/us-east5/publishers/qwen/models/qwen3-next` was not found"
)
UPSTREAM = (
    "litellm.BadRequestError: OpenrouterException - Upstream error from DeepInfra: "
    "unexpected end of data: line 1 column 255659 (char 255658)"
)
TIMEOUT = "litellm.Timeout: Connection timed out after None seconds."


@pytest.mark.parametrize(
    "error_type, error_message, expected",
    [
        ("NotFoundError", NOT_FOUND, InfraError.PROVIDER),
        ("BadRequestError", UPSTREAM, InfraError.PROVIDER),
        (
            "ServiceUnavailableError",
            "litellm.ServiceUnavailableError: overloaded",
            InfraError.PROVIDER,
        ),
        (
            "APIConnectionError",
            "litellm.APIConnectionError: connection reset",
            InfraError.PROVIDER,
        ),
        ("Timeout", TIMEOUT, InfraError.TIMEOUT),
        ("ReadTimeout", "The read operation timed out", InfraError.TIMEOUT),
        ("RateLimitError", "litellm.RateLimitError: 429", InfraError.TIMEOUT),
        # A plain bad request is our bug, not the provider's.
        (
            "BadRequestError",
            "litellm.BadRequestError: AnthropicException - invalid tool schema",
            None,
        ),
        # Same class name from a non-litellm library is not a provider error.
        ("NotFoundError", "pod payment-api not found", None),
        ("AssertionError", "Test 09 failed (score: 0)", None),
        (None, None, None),
    ],
)
def test_classify_infra_error(error_type, error_message, expected):
    assert classify_infra_error(error_type, error_message) is expected


def test_terminal_throttling_is_a_timeout():
    assert classify_infra_error(None, None, failed_due_to_throttling=True) is (
        InfraError.TIMEOUT
    )


def _test(case, model, outcome, error_type=None, error_message=None, tags=None):
    props = [
        {"model": model},
        {"clean_test_case_id": case},
        {"tags": tags or ["regression"]},
    ]
    if error_type:
        props += [{"error_type": error_type}, {"error_message": error_message}]
        props.append({"actual": "Test not executed"})
    return {
        "nodeid": f"tests/llm/test_ask_holmes.py::test_ask_holmes[{case}-{model}-default]",
        "outcome": outcome,
        "user_properties": props,
        "call": {"duration": 10.0},
    }


def _results():
    model = "qwen"
    tests = [_test(f"{i:02d}_pass", model, "passed") for i in range(11)]
    tests += [_test(f"{i:02d}_fail", model, "failed") for i in range(20, 24)]
    tests += [
        _test("30_nf", model, "failed", "NotFoundError", NOT_FOUND),
        _test("31_up", model, "failed", "BadRequestError", UPSTREAM),
        _test("32_to", model, "failed", "Timeout", TIMEOUT),
    ]
    return {"tests": tests}


def test_summary_table_excludes_infra_errors_from_success_rate():
    table = generate_summary_table(_results())
    # Pass | Fail | Skip/Error | Total | Success Rate
    assert "| qwen | 11 | 4 | 3 | 18 | 🟡 73% (11/15) |" in table


def test_heatmap_marks_provider_errors_and_timeouts(monkeypatch):
    # Cells are links only when a Braintrust key is configured.
    monkeypatch.setattr("tests.generate_eval_report.BRAINTRUST_API_KEY", "test-key")
    heatmap = generate_eval_dashboard_heatmap(_results())
    row = {line.split("**")[1]: line for line in heatmap.splitlines() if "**3" in line}
    # Cells link to Braintrust: "[emoji](url)"
    assert "| [⚠️](" in row["30_nf"]
    assert "| [⚠️](" in row["31_up"]
    assert "| [⏱️](" in row["32_to"]
    assert "| 🟡 73% (11/15) |" in heatmap  # SUMMARY row


def test_tag_table_excludes_infra_errors():
    table = generate_model_by_tag_table(_results())
    assert "🟡 73% (11/15)" in table
    assert "⚠️ 3 skipped" in table


def test_latency_table_skips_infra_errors():
    table = generate_latency_comparison_table(_results())
    assert "3 test(s) excluded" in table


def test_infra_errors_section_lists_each_error():
    section = generate_infra_errors_section(_results())
    assert "## Provider Errors and Timeouts" in section
    assert "| ⚠️ | 30_nf | qwen | `NotFoundError`: litellm.NotFoundError" in section
    assert "| ⚠️ | 31_up | qwen | `BadRequestError`:" in section
    assert "| ⏱️ | 32_to | qwen | `Timeout`:" in section
    assert "20_fail" not in section


def test_infra_errors_section_empty_without_infra_errors():
    assert generate_infra_errors_section({"tests": [_test("a", "m", "failed")]}) == ""


def test_status_emoji_uses_rate_when_a_real_run_exists():
    tests = [
        _test("x", "m", "failed", "Timeout", TIMEOUT),
        _test("x", "m", "failed"),
    ]
    stats = {"tests": tests, "infra_errors": 1}
    assert get_test_status_emoji(stats, 2, 0, 0, 0) == "🔴"


def test_github_status_does_not_count_provider_error_as_regression():
    result = {
        "status": "failed",
        "actual_correctness_score": 0,
        "error_type": "NotFoundError",
        "error_message": NOT_FOUND,
    }
    status = TestStatus(result)
    assert status.infra_error is InfraError.PROVIDER
    assert status.is_regression is False
    assert status.markdown_symbol == ":construction:"
    assert status.short_status == "PROVIDER ERROR"


def test_github_status_timeout():
    status = TestStatus(
        {"status": "failed", "error_type": "Timeout", "error_message": TIMEOUT}
    )
    assert status.markdown_symbol == ":hourglass:"
    assert status.is_regression is False
