import json

import pytest

from run_benchmarks_local import any_test_ran


@pytest.mark.parametrize(
    "summary, ran",
    [
        ({"passed": 151, "failed": 25, "total": 176, "collected": 176}, True),
        ({"failed": 2, "error": 3, "total": 5, "collected": 5}, True),
        # Oct 4 2026 weekly run: every xdist worker failed collection
        ({"total": 0, "collected": 0}, False),
        ({"error": 4, "total": 4, "collected": 4}, False),
    ],
)
def test_any_test_ran(tmp_path, summary, ran):
    report = tmp_path / "eval_results.json"
    report.write_text(json.dumps({"summary": summary}))
    assert any_test_ran(report) is ran


def test_any_test_ran_without_report(tmp_path):
    assert any_test_ran(tmp_path / "eval_results.json") is False
