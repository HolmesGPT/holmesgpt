"""Publishing a benchmark run through the eval/Braintrust pipeline.

The point of these tests is that reporting cannot change a result and cannot
break a build: a contributor with no Braintrust credentials must get the same
numbers as CI, and a Braintrust outage must not turn a passing benchmark red.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from holmes.core.investigation_path.calibration_model import CalibrationModel
from holmes.core.investigation_path.metrics import CaseOutcome, score_cases
from holmes.core.investigation_path.offline_eval import main, run_offline_eval
from holmes.core.investigation_path.reporting import (
    BENCHMARK_SUFFIX,
    benchmark_experiment_name,
    benchmark_markdown,
    case_scores,
    log_benchmark_to_braintrust,
    summary_scores,
)

ANSWERED = CaseOutcome(
    incident_id="HOLD-100",
    abstained=False,
    missing_weights={"logs:pod:<subject>": 1.0, "events:pod:<subject>": 0.5},
    suggested=["logs:pod:<subject>", "metrics:node:cpu"],
    suggestion_confidence={"logs:pod:<subject>": 0.8, "metrics:node:cpu": 0.6},
    latency_ms=1.0,
)

ABSTAINED = CaseOutcome(
    incident_id="HOLD-101",
    abstained=True,
    abstain_reason="no_candidates",
    missing_weights={"logs:pod:<subject>": 1.0},
    latency_ms=2.0,
)

SILENT = CaseOutcome(
    incident_id="HOLD-102",
    abstained=False,
    missing_weights={"logs:pod:<subject>": 1.0},
    latency_ms=3.0,
)


class FakeSpan:
    def __init__(self, name, sink):
        self.name = name
        self._sink = sink

    def log(self, **kwargs):
        self._sink.append({"name": self.name, **kwargs})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeExperiment:
    def __init__(self):
        self.flushed = False

    def flush(self):
        self.flushed = True


class FakeTracer:
    """Stands in for BraintrustTracer with a key present."""

    def __init__(self, experiment=None, url="https://braintrust.example/exp"):
        self.logged = []
        self.experiment = experiment if experiment is not None else FakeExperiment()
        self.experiment_name = None
        self.experiment_metadata = None
        self._url = url

    def start_experiment(self, experiment_name=None, additional_metadata=None):
        self.experiment_name = experiment_name
        self.experiment_metadata = additional_metadata
        return self.experiment

    def start_trace(self, name, span_type=None):
        return FakeSpan(name, self.logged)

    def get_trace_url(self):
        return self._url


class TestCaseScores:
    def test_an_answer_is_scored_on_both_recall_and_precision(self):
        scores = case_scores(ANSWERED)
        assert scores["answered"] == 1.0
        assert scores["path_recall"] == pytest.approx(1.0 / 1.5)
        assert scores["suggestion_precision"] == 0.5

    def test_an_abstention_scores_zero_recall(self):
        scores = case_scores(ABSTAINED)
        assert scores["answered"] == 0.0
        assert scores["path_recall"] == 0.0

    def test_an_abstention_is_not_charged_for_precision(self):
        """It made no claim, so a precision of 0 would be a lie about it."""
        assert "suggestion_precision" not in case_scores(ABSTAINED)

    def test_answering_with_nothing_is_not_charged_for_precision_either(self):
        assert "suggestion_precision" not in case_scores(SILENT)

    def test_recall_of_a_case_with_nothing_missing_is_not_a_divide_by_zero(self):
        clean = CaseOutcome(incident_id="HOLD-103", abstained=False)
        assert case_scores(clean)["path_recall"] == 0.0


class TestSummaryScores:
    @pytest.fixture(scope="class")
    def metrics(self):
        return score_cases([ANSWERED, ABSTAINED, SILENT])

    def test_every_reported_score_is_a_probability(self, metrics):
        for name, value in summary_scores(metrics).items():
            assert 0.0 <= value <= 1.0, name

    def test_costs_are_not_reported_as_scores(self, metrics):
        """Braintrust averages scores; latency and bytes would be nonsense there."""
        names = summary_scores(metrics)
        assert "latency_p95_ms" not in names
        assert "bytes_per_incident" not in names


class TestExperimentNaming:
    def test_the_benchmark_gets_its_own_experiment(self):
        """Sharing one with ask_holmes would average deterministic scores into
        a model-scored correctness number."""
        assert benchmark_experiment_name("run-7") == f"run-7-{BENCHMARK_SUFFIX}"

    def test_the_name_falls_back_to_the_ambient_experiment_id(self, monkeypatch):
        monkeypatch.setenv("EXPERIMENT_ID", "github-42")
        assert benchmark_experiment_name() == f"github-42-{BENCHMARK_SUFFIX}"


class TestBraintrustLogging:
    def test_without_credentials_it_does_nothing_and_says_so(self, monkeypatch):
        monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
        metrics = score_cases([ANSWERED, ABSTAINED])
        assert log_benchmark_to_braintrust(metrics, [ANSWERED, ABSTAINED]) is None

    def test_one_span_per_case_plus_a_summary(self):
        tracer = FakeTracer()
        metrics = score_cases([ANSWERED, ABSTAINED, SILENT])
        with patch(
            "holmes.core.investigation_path.reporting.TracingFactory.create_tracer",
            return_value=tracer,
        ):
            url = log_benchmark_to_braintrust(metrics, [ANSWERED, ABSTAINED, SILENT])

        assert url == "https://braintrust.example/exp"
        assert len(tracer.logged) == 4
        assert tracer.logged[-1]["name"] == "path-completeness[summary]"

    def test_a_case_span_carries_what_was_suggested_and_what_was_missing(self):
        tracer = FakeTracer()
        metrics = score_cases([ANSWERED])
        with patch(
            "holmes.core.investigation_path.reporting.TracingFactory.create_tracer",
            return_value=tracer,
        ):
            log_benchmark_to_braintrust(metrics, [ANSWERED])

        span = tracer.logged[0]
        assert span["input"] == "HOLD-100"
        assert span["output"] == ANSWERED.suggested
        assert span["expected"] == sorted(ANSWERED.missing_weights)
        assert span["metadata"]["false_positives"] == ["metrics:node:cpu"]

    def test_an_abstention_is_tagged_so_it_can_be_filtered_out(self):
        tracer = FakeTracer()
        metrics = score_cases([ABSTAINED])
        with patch(
            "holmes.core.investigation_path.reporting.TracingFactory.create_tracer",
            return_value=tracer,
        ):
            log_benchmark_to_braintrust(metrics, [ABSTAINED])
        assert "abstained" in tracer.logged[0]["tags"]

    def test_the_experiment_records_the_zero_llm_call_budget(self):
        tracer = FakeTracer()
        metrics = score_cases([ANSWERED])
        with patch(
            "holmes.core.investigation_path.reporting.TracingFactory.create_tracer",
            return_value=tracer,
        ):
            log_benchmark_to_braintrust(
                metrics, [ANSWERED], CalibrationModel(fitted=False), "run-9"
            )
        assert tracer.experiment_name == f"run-9-{BENCHMARK_SUFFIX}"
        assert tracer.experiment_metadata["llm_calls"] == 0
        assert tracer.experiment_metadata["issue"] == "2046"

    def test_results_are_flushed_before_the_process_exits(self):
        tracer = FakeTracer()
        with patch(
            "holmes.core.investigation_path.reporting.TracingFactory.create_tracer",
            return_value=tracer,
        ):
            log_benchmark_to_braintrust(score_cases([ANSWERED]), [ANSWERED])
        assert tracer.experiment.flushed

    def test_a_reporting_failure_does_not_fail_the_benchmark(self):
        """A Braintrust outage must not read as a policy regression."""
        tracer = FakeTracer()
        tracer.start_trace = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with patch(
            "holmes.core.investigation_path.reporting.TracingFactory.create_tracer",
            return_value=tracer,
        ):
            assert log_benchmark_to_braintrust(score_cases([ANSWERED]), [ANSWERED]) is None


class TestMarkdown:
    @pytest.fixture(scope="class")
    def report(self):
        metrics = score_cases([ANSWERED, ABSTAINED, SILENT])
        return benchmark_markdown(metrics, [ANSWERED, ABSTAINED, SILENT])

    def test_it_states_that_nothing_is_wired_to_runtime(self, report):
        assert "Offline only" in report
        assert "2046" in report

    def test_every_case_appears(self, report):
        for outcome in (ANSWERED, ABSTAINED, SILENT):
            assert outcome.incident_id in report

    def test_an_abstention_shows_its_reason(self, report):
        assert "abstained (no_candidates)" in report

    def test_answering_with_nothing_is_not_shown_as_an_answer(self, report):
        """0 found and 0 wrong under 'answered' reads like a success."""
        assert "no suggestions" in report

    def test_the_tables_are_well_formed(self, report):
        for line in report.splitlines():
            if line.startswith("|") and not line.startswith("| ---"):
                assert line.endswith("|")

    def test_the_calibration_line_is_one_paragraph(self):
        """A leading space here silently becomes a code block in some renderers."""
        metrics = score_cases([ANSWERED])
        report = benchmark_markdown(
            metrics, [ANSWERED], CalibrationModel(fitted=False), metrics
        )
        assert "Confidence calibration" in report
        assert not any(line.startswith(" ") for line in report.splitlines())

    def test_a_braintrust_link_is_included_when_there_is_one(self):
        report = benchmark_markdown(
            score_cases([ANSWERED]), [ANSWERED], experiment_url="https://bt.example/x"
        )
        assert "[View in Braintrust](https://bt.example/x)" in report

    def test_no_link_is_shown_when_the_run_was_not_logged(self, report):
        assert "View in Braintrust" not in report


class TestCli:
    def test_it_writes_the_report_where_asked(self, tmp_path):
        target = tmp_path / "report.md"
        main(["--markdown", str(target)])
        assert "Investigation path completeness benchmark" in target.read_text()

    def test_the_report_matches_the_run_it_reports_on(self, tmp_path):
        target = tmp_path / "report.md"
        main(["--markdown", str(target)])
        metrics, _, _ = run_offline_eval()
        assert f"| Held-out cases | {metrics.cases} |" in target.read_text()

    def test_it_prints_a_report_without_being_asked_for_a_file(self, capsys):
        main([])
        assert "weighted path recall" in capsys.readouterr().out

    def test_asking_for_braintrust_without_a_key_is_not_an_error(
        self, monkeypatch, capsys, tmp_path
    ):
        monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
        main(["--braintrust", "--markdown", str(tmp_path / "r.md")])
        assert "braintrust:" not in capsys.readouterr().out

    def test_reporting_cannot_change_the_result(self, tmp_path):
        """The CLI must not be a second, drifting implementation of the eval.

        Latency is excluded: it is measured wall clock, so it is the one field
        that is legitimately allowed to differ between two identical runs.
        """
        timing = {"latency_p50_ms", "latency_p95_ms"}
        before, _, _ = run_offline_eval()
        main(["--markdown", str(tmp_path / "r.md")])
        after, _, _ = run_offline_eval()
        assert {k: v for k, v in before.model_dump().items() if k not in timing} == {
            k: v for k, v in after.model_dump().items() if k not in timing
        }


class TestPackaging:
    def test_the_benchmark_is_runnable_as_documented(self):
        """The design doc and CI both invoke it this way."""
        module = Path("holmes/core/investigation_path/offline_eval.py")
        assert module.is_file()
        assert "__main__" in module.read_text()
