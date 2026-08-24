"""Run a retrieval policy over the held-out corpus and score it.

This is the benchmark the reviewer asked for on issue #2046: it exists so that
a retrieval policy can be compared against another one before either is allowed
anywhere near a user-facing investigation. Nothing here imports the
investigation loop, and no LLM is called - `llm_calls` in the result is asserted
to stay at zero so that a future change which adds a model call to this path
shows up as a cost regression rather than a silent one.

Run it directly to print a report:

    poetry run python -m holmes.core.investigation_path.offline_eval
"""

import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from holmes.core.investigation_path.corpus import corpus_bytes_per_incident, load_corpus
from holmes.core.investigation_path.metrics import CaseOutcome, EvalMetrics, score_cases
from holmes.core.investigation_path.retrieval import RetrievalPolicy, retrieve
from holmes.core.investigation_path.schema import IncidentRecord, SignatureLevel
from holmes.core.investigation_path.validator import SuggestionPolicy, validate_path


def evaluate_case(
    case: IncidentRecord,
    pool: Sequence[IncidentRecord],
    retrieval_policy: RetrievalPolicy,
    suggestion_policy: SuggestionPolicy,
    level: SignatureLevel = SignatureLevel.FINE,
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
    )


def run_offline_eval(
    corpus_dir: Optional[Path] = None,
    retrieval_policy: Optional[RetrievalPolicy] = None,
    suggestion_policy: Optional[SuggestionPolicy] = None,
) -> Tuple[EvalMetrics, List[CaseOutcome]]:
    """Score a policy over every held-out incident. Returns metrics and per-case detail."""
    retrieval_policy = retrieval_policy or RetrievalPolicy()
    suggestion_policy = suggestion_policy or SuggestionPolicy()

    pool = load_corpus(corpus_dir, split="corpus")
    holdout = load_corpus(corpus_dir, split="holdout")
    if not pool:
        raise ValueError("Retrieval pool is empty; nothing to evaluate against")
    if not holdout:
        raise ValueError("Held-out set is empty; nothing to evaluate")

    outcomes = [
        evaluate_case(
            case,
            pool,
            retrieval_policy,
            suggestion_policy,
            retrieval_policy.signature_level,
        )
        for case in holdout
    ]
    metrics = score_cases(outcomes, bytes_per_incident=corpus_bytes_per_incident(pool))
    return metrics, outcomes


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


def main() -> None:
    metrics, outcomes = run_offline_eval()
    print("Investigation path completeness - offline eval")
    print("=" * 46)
    print(metrics.render())
    print()
    print(render_case_detail(outcomes))


if __name__ == "__main__":
    main()
