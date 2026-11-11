import json
import subprocess

import pytest

import run_benchmarks_local
from run_benchmarks_local import BenchmarkRunner, any_test_ran


@pytest.mark.parametrize(
    "summary, ran",
    [
        ({"passed": 151, "failed": 25, "total": 176, "collected": 176}, True),
        ({"passed": 1, "total": 1, "collected": 1}, True),
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
    "summary, pytest_exit, exit_code, report_written",
    [
        # xdist: every worker failed collection
        ({"total": 0, "collected": 0}, 1, 5, False),
        # the selection matched no test
        ({"total": 0, "collected": 0}, 5, 5, False),
        # collection error without xdist ("Interrupted: 1 error during collection")
        ({"error": 1, "total": 1, "collected": 0}, 2, 2, False),
        ({"passed": 1, "failed": 1}, 1, 1, True),
    ],
)
def test_run_skips_report_when_no_test_ran(
    tmp_path, monkeypatch, summary, pytest_exit, exit_code, report_written
):
    """No report means CI opens no empty results PR, and a nonzero exit fails the step."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "eval_results.json").write_text(json.dumps({"summary": summary}))
    runner = BenchmarkRunner(models=["m"])
    reports = []
    monkeypatch.setattr(runner, "check_environment", lambda: None)
    monkeypatch.setattr(runner, "setup_environment", lambda: None)
    monkeypatch.setattr(runner, "run_tests", lambda: pytest_exit)
    monkeypatch.setattr(runner, "generate_report", lambda: reports.append(1))
    monkeypatch.setattr(runner, "show_summary", lambda report_file: None)

    assert runner.run() == exit_code
    assert bool(reports) is report_written


def test_run_tests_removes_previous_report(tmp_path, monkeypatch):
    """A report left by an earlier run must not count as this run's results."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "eval_results.json").write_text(json.dumps({"summary": {"passed": 3}}))
    monkeypatch.setattr(
        run_benchmarks_local.subprocess, "run", lambda cmd: subprocess.CompletedProcess(cmd, 4)
    )

    assert BenchmarkRunner(models=["m"]).run_tests() == 4
    assert any_test_ran(tmp_path / "eval_results.json") is False
