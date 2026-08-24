"""Publish the path-completeness benchmark through the existing eval pipeline.

The reviewer on issue #2046 asked for the benchmark to be established in the
eval/Braintrust pipeline rather than bolted onto the product, so that a
retrieval policy has a tracked history before anything user-facing depends on
it. This module is that seam, and it does two things:

- `benchmark_markdown` renders a run for a pull request comment, in the same
  place the LLM evals post theirs.
- `log_benchmark_to_braintrust` writes the run to Braintrust as its own
  experiment, one span per held-out incident plus a summary span.

Both are reporting only. Neither imports the investigation loop, and the
Braintrust path degrades to a no-op when `BRAINTRUST_API_KEY` is unset, so the
benchmark stays runnable by a contributor with no credentials.

The benchmark gets its own experiment name rather than joining the ask_holmes
run: its scores are deterministic and would otherwise be averaged into a
correctness number that is measuring something else entirely.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from holmes.core.investigation_path.calibration import CalibrationModel
from holmes.core.investigation_path.metrics import CaseOutcome, EvalMetrics
from holmes.core.tracing import SpanType, TracingFactory, get_experiment_name

BENCHMARK_SUFFIX = "investigation-path"
BENCHMARK_TAGS = ["investigation-path", "benchmark", "offline"]


def benchmark_experiment_name(base: Optional[str] = None) -> str:
    """Name the experiment after the run that produced it, kept separate.

    Sharing `EXPERIMENT_ID` with the ask_holmes evals would put deterministic
    rows in the same average as model-scored ones.
    """
    return f"{base or get_experiment_name()}-{BENCHMARK_SUFFIX}"


def case_scores(outcome: CaseOutcome) -> Dict[str, float]:
    """Scores for one held-out incident.

    An abstention scores 0 recall and is not charged for precision - it made no
    claim. `answered` is logged alongside so the two can never be read apart.
    """
    scores = {
        "answered": 0.0 if outcome.abstained else 1.0,
        "path_recall": outcome.weighted_recall,
    }
    if not outcome.abstained and outcome.suggested:
        scores["suggestion_precision"] = outcome.precision
    return scores


def case_metadata(outcome: CaseOutcome) -> Dict[str, Any]:
    return {
        "incident_id": outcome.incident_id,
        "abstained": outcome.abstained,
        "abstain_reason": outcome.abstain_reason,
        "suggested": outcome.suggested,
        "true_positives": outcome.true_positives,
        "false_positives": outcome.false_positives,
        "missing_signatures": sorted(outcome.missing_weights),
        "missing_weight": round(outcome.missing_weight, 4),
        "recovered_weight": round(outcome.recovered_weight, 4),
        "confidence": {k: round(v, 4) for k, v in outcome.suggestion_confidence.items()},
        "latency_ms": round(outcome.latency_ms, 3),
        "llm_calls": outcome.llm_calls,
    }


def summary_scores(metrics: EvalMetrics) -> Dict[str, float]:
    """The aggregate numbers worth tracking run over run.

    Latency and storage are deliberately absent: they are costs, not scores,
    and Braintrust averages scores across rows.
    """
    return {
        "weighted_path_recall": metrics.weighted_path_recall,
        "weighted_path_recall_when_answering": metrics.weighted_path_recall_when_answering,
        "suggestion_precision": metrics.suggestion_precision,
        "weighted_suggestion_precision": metrics.weighted_suggestion_precision,
        "abstention_rate": metrics.abstention_rate,
    }


def summary_metadata(
    metrics: EvalMetrics, calibration: Optional[CalibrationModel] = None
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "cases": metrics.cases,
        "answered_cases": metrics.answered_cases,
        "abstain_reasons": metrics.abstain_reasons,
        "false_positive_burden": round(metrics.false_positive_burden, 4),
        "expected_calibration_error": round(metrics.expected_calibration_error, 4),
        "brier_score": round(metrics.brier_score, 4),
        "latency_p50_ms": round(metrics.latency_p50_ms, 3),
        "latency_p95_ms": round(metrics.latency_p95_ms, 3),
        "bytes_per_incident": round(metrics.bytes_per_incident, 1),
        "llm_calls": metrics.llm_calls,
    }
    if calibration is not None:
        metadata["calibration"] = calibration.describe()
        metadata["calibration_samples"] = calibration.samples
    return metadata


def log_benchmark_to_braintrust(
    metrics: EvalMetrics,
    outcomes: Sequence[CaseOutcome],
    calibration: Optional[CalibrationModel] = None,
    experiment_name: Optional[str] = None,
) -> Optional[str]:
    """Write one benchmark run to Braintrust. Returns the experiment URL, or None.

    Returns None when tracing is unavailable, which is the normal case locally.
    Failures here are logged and swallowed: a reporting outage must not turn a
    passing benchmark into a red build.
    """
    tracer = TracingFactory.create_tracer("braintrust")
    experiment = tracer.start_experiment(
        experiment_name=benchmark_experiment_name(experiment_name),
        additional_metadata={
            "benchmark": BENCHMARK_SUFFIX,
            "issue": "2046",
            **summary_metadata(metrics, calibration),
        },
    )
    if experiment is None:
        logging.debug("Braintrust not configured; investigation path benchmark not logged")
        return None

    try:
        for outcome in outcomes:
            with tracer.start_trace(
                name=f"path-completeness[{outcome.incident_id}]", span_type=SpanType.EVAL
            ) as span:
                span.log(
                    input=outcome.incident_id,
                    output=outcome.suggested,
                    expected=sorted(outcome.missing_weights),
                    scores=case_scores(outcome),
                    metadata=case_metadata(outcome),
                    tags=BENCHMARK_TAGS
                    + (["abstained"] if outcome.abstained else []),
                )

        with tracer.start_trace(
            name="path-completeness[summary]", span_type=SpanType.EVAL
        ) as span:
            span.log(
                input="offline eval over the held-out corpus",
                output=metrics.render(),
                scores=summary_scores(metrics),
                metadata=summary_metadata(metrics, calibration),
                tags=BENCHMARK_TAGS + ["summary"],
            )

        flush = getattr(experiment, "flush", None)
        if callable(flush):
            flush()
        return tracer.get_trace_url()
    except Exception:
        logging.exception("Failed to log investigation path benchmark to Braintrust")
        return None


def _summary_rows(metrics: EvalMetrics) -> List[Tuple[str, str]]:
    return [
        ("Held-out cases", f"{metrics.cases}"),
        ("Answered", f"{metrics.answered_cases}"),
        ("Abstention rate", f"{metrics.abstention_rate:.2f}"),
        ("Weighted path recall", f"{metrics.weighted_path_recall:.2f}"),
        ("Weighted path recall (answering)", f"{metrics.weighted_path_recall_when_answering:.2f}"),
        ("Suggestion precision", f"{metrics.suggestion_precision:.2f}"),
        ("Weighted suggestion precision", f"{metrics.weighted_suggestion_precision:.2f}"),
        ("False positives per answer", f"{metrics.false_positive_burden:.2f}"),
        ("Expected calibration error", f"{metrics.expected_calibration_error:.3f}"),
        ("Brier score", f"{metrics.brier_score:.3f}"),
        ("Latency p50 / p95 (ms)", f"{metrics.latency_p50_ms:.1f} / {metrics.latency_p95_ms:.1f}"),
        ("Bytes per stored incident", f"{metrics.bytes_per_incident:.0f}"),
        ("LLM calls", f"{metrics.llm_calls}"),
    ]


def benchmark_markdown(
    metrics: EvalMetrics,
    outcomes: Sequence[CaseOutcome],
    calibration: Optional[CalibrationModel] = None,
    raw_metrics: Optional[EvalMetrics] = None,
    experiment_url: Optional[str] = None,
) -> str:
    """Render a benchmark run for a pull request comment."""
    lines = [
        "## Investigation path completeness benchmark",
        "",
        "Offline only. No runtime behaviour depends on these numbers "
        "([#2046](https://github.com/HolmesGPT/holmesgpt/issues/2046)).",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    lines += [f"| {label} | {value} |" for label, value in _summary_rows(metrics)]

    if calibration is not None:
        calibration_line = f"**Confidence calibration:** {calibration.describe()}"
        if raw_metrics is not None:
            calibration_line += (
                f". Calibration error {raw_metrics.expected_calibration_error:.3f} → "
                f"{metrics.expected_calibration_error:.3f}, "
                f"Brier {raw_metrics.brier_score:.3f} → {metrics.brier_score:.3f}"
            )
        lines += ["", calibration_line + "."]

    lines += [
        "",
        "<details><summary>Per-incident results</summary>",
        "",
        "| Incident | Result | Found | Wrong | Skipped checks |",
        "| --- | --- | --- | --- | --- |",
    ]
    for outcome in outcomes:
        if outcome.abstained:
            result = f"abstained ({outcome.abstain_reason})"
        elif not outcome.suggested:
            # Retrieval found a match but every candidate check was filtered out.
            # Reads identically to an abstention to a human, so say so.
            result = "no suggestions"
        else:
            result = "answered"
        lines.append(
            f"| {outcome.incident_id} | {result} | {len(outcome.true_positives)} | "
            f"{len(outcome.false_positives)} | {len(outcome.missing_weights)} |"
        )
    lines += ["", "</details>"]

    if metrics.abstain_reasons:
        reasons = ", ".join(
            f"{reason}: {count}" for reason, count in sorted(metrics.abstain_reasons.items())
        )
        lines += ["", f"**Abstained because** {reasons}."]

    if experiment_url:
        lines += ["", f"[View in Braintrust]({experiment_url})"]

    return "\n".join(lines) + "\n"
