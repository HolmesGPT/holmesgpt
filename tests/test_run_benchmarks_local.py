import json

import pytest

from run_benchmarks_local import BenchmarkRunner, any_test_ran


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


@pytest.mark.parametrize(
    "summary, exit_code, report_written",
    [({"total": 0, "collected": 0}, 5, False), ({"passed": 1, "failed": 1}, 1, True)],
)
def test_run_skips_report_when_no_test_ran(tmp_path, monkeypatch, summary, exit_code, report_written):
    """No report means CI opens no empty results PR, and exit 5 fails the step."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "eval_results.json").write_text(json.dumps({"summary": summary}))
    runner = BenchmarkRunner(models=["m"])
    reports = []
    monkeypatch.setattr(runner, "check_environment", lambda: None)
    monkeypatch.setattr(runner, "setup_environment", lambda: None)
    monkeypatch.setattr(runner, "run_tests", lambda: 1)
    monkeypatch.setattr(runner, "generate_report", lambda: reports.append(1))
    monkeypatch.setattr(runner, "show_summary", lambda report_file: None)

    assert runner.run() == exit_code
    assert bool(reports) is report_written
