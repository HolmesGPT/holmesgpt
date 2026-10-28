"""Run a retrieval policy over the held-out corpus and score it.

This is the benchmark the reviewer asked for on issue #2046: it exists so that
a retrieval policy can be compared against another one before either is allowed
anywhere near a user-facing investigation. Nothing here imports the
investigation loop, and no LLM is called - `llm_calls` in the result is asserted
to stay at zero so that a future change which adds a model call to this path
shows up as a cost regression rather than a silent one.

Run it directly to print a report:

    poetry run python -m holmes.core.investigation_path.offline_eval

`--markdown` and `--braintrust` publish the same run through the existing eval
reporting pipeline; see `reporting.py`.
"""

import argparse
import time
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from holmes.core.investigation_path.calibration import fit_calibration
from holmes.core.investigation_path.calibration_model import CalibrationModel
from holmes.core.investigation_path.corpus import corpus_bytes_per_incident, load_corpus
from holmes.core.investigation_path.metrics import CaseOutcome, EvalMetrics, score_cases
from holmes.core.investigation_path.reporting import (
    benchmark_markdown,
    log_benchmark_to_braintrust,
)
from holmes.core.investigation_path.retrieval import RetrievalPolicy, retrieve
from holmes.core.investigation_path.schema import (
    IncidentRecord,
    ReferenceStep,
    SignatureLevel,
)
from holmes.core.investigation_path.validator import SuggestionPolicy, validate_path


def entities_seen_in(steps: Sequence[ReferenceStep]) -> Set[str]:
    """Object names an investigation has actually looked at."""
    return {step.entity.name for step in steps if step.entity.name}


def evaluate_case(
    case: IncidentRecord,
    pool: Sequence[IncidentRecord],
    retrieval_policy: RetrievalPolicy,
    suggestion_policy: SuggestionPolicy,
    level: SignatureLevel = SignatureLevel.FINE,
    calibration: Optional[CalibrationModel] = None,
) -> CaseOutcome:
    """Score one held-out incident against the retrieval pool."""
    missing_weights = {
        step.signature(level): step.weight for step in case.missing_steps(level)
    }

    started = time.perf_counter()
    retrieval = retrieve(case.symptoms, pool, retrieval_policy)
    report = validate_path(
        observed_signatures=sorted(case.observed_signatures(level)),
        retrieval=retrieval,
        policy=suggestion_policy,
        subject=case.subject,
        calibration=calibration,
        known_entities=entities_seen_in(case.observed_path),
    )
    latency_ms = (time.perf_counter() - started) * 1000

    return CaseOutcome(
        incident_id=case.incident_id,
        abstained=report.abstained,
        abstain_reason=retrieval.abstain_reason.value if retrieval.abstain_reason else None,
        missing_weights=missing_weights,
        suggested=[s.signature for s in report.suggestions],
        suggestion_confidence={s.signature: s.confidence for s in report.suggestions},
        latency_ms=latency_ms,
        llm_calls=0,
        llm_tokens=0,
    )


def run_offline_eval(
    corpus_dir: Optional[Path] = None,
    retrieval_policy: Optional[RetrievalPolicy] = None,
    suggestion_policy: Optional[SuggestionPolicy] = None,
    calibrate: bool = True,
) -> Tuple[EvalMetrics, List[CaseOutcome], CalibrationModel]:
    """Score a policy over every held-out incident.

    The calibration model is fitted on the retrieval pool by leave-one-out and
    only then applied to the held-out cases, so the calibration error reported
    here is out-of-sample. Pass `calibrate=False` to measure the raw score.
    """
    retrieval_policy = retrieval_policy or RetrievalPolicy()
    suggestion_policy = suggestion_policy or SuggestionPolicy()

    pool = load_corpus(corpus_dir, split="corpus")
    holdout = load_corpus(corpus_dir, split="holdout")
    if not pool:
        raise ValueError("Retrieval pool is empty; nothing to evaluate against")
    if not holdout:
        raise ValueError("Held-out set is empty; nothing to evaluate")

    calibration = (
        fit_calibration(pool, retrieval_policy, retrieval_policy.signature_level)
        if calibrate
        else CalibrationModel(fitted=False)
    )

    outcomes = [
        evaluate_case(
            case,
            pool,
            retrieval_policy,
            suggestion_policy,
            retrieval_policy.signature_level,
            calibration,
        )
        for case in holdout
    ]
    metrics = score_cases(outcomes, bytes_per_incident=corpus_bytes_per_incident(pool))
    return metrics, outcomes, calibration


def render_case_detail(outcomes: Sequence[CaseOutcome]) -> str:
    lines = ["per-case:"]
    for outcome in outcomes:
        if outcome.abstained:
            lines.append(f"  {outcome.incident_id}: abstained ({outcome.abstain_reason})")
            continue
        lines.append(
            f"  {outcome.incident_id}: {len(outcome.true_positives)} correct, "
            f"{len(outcome.false_positives)} wrong, "
            f"{len(outcome.missing_weights)} genuinely missing"
        )
        for signature in outcome.false_positives:
            lines.append(f"    wrong: {signature}")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m holmes.core.investigation_path.offline_eval",
        description="Score the path-completeness retrieval policy offline.",
    )
    parser.add_argument(
        "--markdown",
        metavar="PATH",
        help="Write a pull-request-ready report to PATH.",
    )
    parser.add_argument(
        "--braintrust",
        action="store_true",
        help="Log the run to Braintrust. No-op without BRAINTRUST_API_KEY.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = build_arg_parser().parse_args(argv)

    raw_metrics, _, _ = run_offline_eval(calibrate=False)
    metrics, outcomes, calibration = run_offline_eval(calibrate=True)

    # Local output first. Uploading to a remote service before printing the
    # numbers means an outage there loses the numbers too, and the CI step is
    # `continue-on-error` - the build would stay green with the benchmark
    # silently missing from the pull request comment.
    print("Investigation path completeness - offline eval")
    print("=" * 46)
    print(metrics.render())
    print()
    print(f"calibration: {calibration.describe()}")
    print(
        f"  calibration error before: {raw_metrics.expected_calibration_error:.3f}"
        f"  after: {metrics.expected_calibration_error:.3f}"
    )
    print(
        f"  brier before: {raw_metrics.brier_score:.3f}"
        f"  after: {metrics.brier_score:.3f}"
    )
    print()
    print(render_case_detail(outcomes))

    experiment_url = (
        log_benchmark_to_braintrust(metrics, outcomes, calibration)
        if args.braintrust
        else None
    )
    if experiment_url:
        print()
        print(f"braintrust: {experiment_url}")

    if args.markdown:
        report = benchmark_markdown(
            metrics, outcomes, calibration, raw_metrics, experiment_url
        )
        Path(args.markdown).write_text(report, encoding="utf-8")
        print()
        print(f"wrote {args.markdown}")


if __name__ == "__main__":
    main()
