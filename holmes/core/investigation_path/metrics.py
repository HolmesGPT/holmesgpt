"""How a path-completeness retrieval policy is scored offline.

Definitions, stated once so results from different runs are comparable.

For a held-out incident, the human-validated record gives a `reference_path`
(the checks that should have been run) and an `observed_path` (what the
investigation actually did). The ground-truth missing set `M` is
`reference_path - observed_path`, and each of its steps carries a weight in
[0, 1] saying how much skipping it cost. The policy answers with a suggestion
set `S`, or abstains.

- **Weighted path recall** - share of `M`'s weight that appears in `S`, summed
  over every held-out incident. Abstentions contribute 0, so this is the
  coverage number: it goes down when the policy stays quiet.
- **Weighted path recall when answering** - the same, restricted to incidents
  where the policy answered. Reported alongside recall because the two move in
  opposite directions as the abstention threshold changes, and neither is
  meaningful alone.
- **Suggestion precision** - share of all suggestions that were genuinely
  missing. `weighted_suggestion_precision` credits a true positive by its
  weight and charges a false positive a flat 1.0, so surfacing a trivial check
  cannot pay for a wrong one.
- **False-positive burden** - mean count of wrong suggestions per answered
  incident. Precision alone hides this: 80% precision over 25 suggestions is a
  far worse experience than 80% over 4.
- **Abstention rate** - share of incidents where the policy declined. Read with
  recall and precision; on its own it is neither good nor bad.
- **Expected calibration error / Brier score** - whether the confidence
  attached to a suggestion means anything. ECE bins suggestions by confidence
  and compares each bin's stated confidence to the share that were actually
  correct. This is the check that a raw similarity score cannot pass by itself.
- **Latency** - p50/p95 wall clock per validation.
- **Storage cost** - mean serialized bytes per stored incident record.
- **LLM calls** - expected to be 0. Tracked so that a future change which
  quietly adds a model call to this path shows up as a cost regression.
"""

import math
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

DEFAULT_CALIBRATION_BINS = 10


class CaseOutcome(BaseModel):
    """Everything measured for one held-out incident."""

    incident_id: str
    abstained: bool
    abstain_reason: Optional[str] = None
    missing_weights: Dict[str, float] = Field(
        default_factory=dict, description="Ground-truth missing signatures and their weights."
    )
    suggested: List[str] = Field(default_factory=list)
    suggestion_confidence: Dict[str, float] = Field(default_factory=dict)
    latency_ms: float = 0.0
    llm_calls: int = 0

    @property
    def true_positives(self) -> List[str]:
        return [s for s in self.suggested if s in self.missing_weights]

    @property
    def false_positives(self) -> List[str]:
        return [s for s in self.suggested if s not in self.missing_weights]

    @property
    def missing_weight(self) -> float:
        """Total weight of the checks this investigation actually skipped."""
        return sum(self.missing_weights.values())

    @property
    def recovered_weight(self) -> float:
        """Weight of the skipped checks the policy surfaced."""
        return sum(self.missing_weights[s] for s in self.true_positives)

    @property
    def weighted_recall(self) -> float:
        """Share of this incident's missing weight that the policy found."""
        return _safe_divide(self.recovered_weight, self.missing_weight)

    @property
    def precision(self) -> float:
        """Share of this incident's suggestions that were genuinely missing."""
        return _safe_divide(len(self.true_positives), len(self.suggested))


class EvalMetrics(BaseModel):
    """Scores for one retrieval policy over one held-out set."""

    cases: int
    answered_cases: int
    abstention_rate: float
    abstain_reasons: Dict[str, int] = Field(default_factory=dict)

    weighted_path_recall: float
    weighted_path_recall_when_answering: float
    suggestion_precision: float
    weighted_suggestion_precision: float
    false_positive_burden: float

    expected_calibration_error: float
    brier_score: float

    latency_p50_ms: float
    latency_p95_ms: float
    bytes_per_incident: float
    llm_calls: int

    def render(self) -> str:
        """Plain-text report, so an eval run can be diffed between policies."""
        rows = [
            ("cases", f"{self.cases}"),
            ("answered", f"{self.answered_cases}"),
            ("abstention rate", f"{self.abstention_rate:.2f}"),
            ("weighted path recall", f"{self.weighted_path_recall:.2f}"),
            ("  ... when answering", f"{self.weighted_path_recall_when_answering:.2f}"),
            ("suggestion precision", f"{self.suggestion_precision:.2f}"),
            ("  ... weighted", f"{self.weighted_suggestion_precision:.2f}"),
            ("false positives per answer", f"{self.false_positive_burden:.2f}"),
            ("expected calibration error", f"{self.expected_calibration_error:.3f}"),
            ("brier score", f"{self.brier_score:.3f}"),
            ("latency p50 (ms)", f"{self.latency_p50_ms:.2f}"),
            ("latency p95 (ms)", f"{self.latency_p95_ms:.2f}"),
            ("bytes per incident", f"{self.bytes_per_incident:.0f}"),
            ("llm calls", f"{self.llm_calls}"),
        ]
        width = max(len(label) for label, _ in rows)
        lines = [f"{label.ljust(width)}  {value}" for label, value in rows]
        if self.abstain_reasons:
            lines.append("")
            lines.append("abstain reasons:")
            for reason, count in sorted(self.abstain_reasons.items()):
                lines.append(f"  {reason}: {count}")
        return "\n".join(lines)


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile.

    Nearest-rank rather than interpolation because `round()` uses banker's
    rounding, which makes the p50 of an even-length sample depend on whether
    the midpoint index happens to be even.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(fraction * len(ordered))
    index = min(len(ordered) - 1, max(0, rank - 1))
    return ordered[index]


def expected_calibration_error(
    confidences: Sequence[float],
    correct: Sequence[bool],
    bins: int = DEFAULT_CALIBRATION_BINS,
) -> float:
    """Mean gap between stated confidence and observed correctness, weighted by bin size."""
    if not confidences:
        return 0.0
    buckets: Dict[int, List[int]] = {}
    for confidence, is_correct in zip(confidences, correct):
        index = min(bins - 1, max(0, int(confidence * bins)))
        buckets.setdefault(index, []).append(1 if is_correct else 0)

    total = len(confidences)
    error = 0.0
    for index, outcomes in buckets.items():
        observed = sum(outcomes) / len(outcomes)
        stated = (index + 0.5) / bins
        error += (len(outcomes) / total) * abs(observed - stated)
    return error


def brier_score(confidences: Sequence[float], correct: Sequence[bool]) -> float:
    """Mean squared error between stated confidence and the 0/1 outcome."""
    if not confidences:
        return 0.0
    return sum(
        (confidence - (1.0 if is_correct else 0.0)) ** 2
        for confidence, is_correct in zip(confidences, correct)
    ) / len(confidences)


def score_cases(cases: Sequence[CaseOutcome], bytes_per_incident: float = 0.0) -> EvalMetrics:
    """Aggregate per-incident outcomes into the reported metric set."""
    if not cases:
        raise ValueError("Cannot score an empty held-out set")

    answered = [case for case in cases if not case.abstained]

    total_missing_weight = 0.0
    recovered_weight = 0.0
    answered_missing_weight = 0.0
    answered_recovered_weight = 0.0

    true_positive_weight = 0.0
    true_positives = 0
    false_positives = 0

    confidences: List[float] = []
    correct: List[bool] = []
    abstain_reasons: Dict[str, int] = {}

    for case in cases:
        case_missing_weight = case.missing_weight
        case_recovered = case.recovered_weight

        total_missing_weight += case_missing_weight
        recovered_weight += case_recovered

        if case.abstained:
            reason = case.abstain_reason or "unspecified"
            abstain_reasons[reason] = abstain_reasons.get(reason, 0) + 1
            continue

        answered_missing_weight += case_missing_weight
        answered_recovered_weight += case_recovered

        true_positives += len(case.true_positives)
        false_positives += len(case.false_positives)
        true_positive_weight += case_recovered

        for signature in case.suggested:
            confidences.append(case.suggestion_confidence.get(signature, 0.0))
            correct.append(signature in case.missing_weights)

    suggested_total = true_positives + false_positives

    return EvalMetrics(
        cases=len(cases),
        answered_cases=len(answered),
        abstention_rate=(len(cases) - len(answered)) / len(cases),
        abstain_reasons=abstain_reasons,
        weighted_path_recall=_safe_divide(recovered_weight, total_missing_weight),
        weighted_path_recall_when_answering=_safe_divide(
            answered_recovered_weight, answered_missing_weight
        ),
        suggestion_precision=_safe_divide(true_positives, suggested_total),
        weighted_suggestion_precision=_safe_divide(
            true_positive_weight, true_positive_weight + false_positives
        ),
        false_positive_burden=_safe_divide(false_positives, len(answered)),
        expected_calibration_error=expected_calibration_error(confidences, correct),
        brier_score=brier_score(confidences, correct),
        latency_p50_ms=_percentile([case.latency_ms for case in cases], 0.50),
        latency_p95_ms=_percentile([case.latency_ms for case in cases], 0.95),
        bytes_per_incident=bytes_per_incident,
        llm_calls=sum(case.llm_calls for case in cases),
    )


def _safe_divide(numerator: float, denominator: float) -> float:
    """Return 0.0 for an empty denominator rather than raising.

    An empty denominator means the quantity was never exercised (no missing
    checks, or no suggestions), which is not the same as a failure.
    """
    if denominator == 0:
        return 0.0
    return numerator / denominator
