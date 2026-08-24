"""Turn the raw evidence score into something that means what it says.

The raw score is a product of four terms below 1 (symptom similarity, root-cause
agreement, match support, and per-check support). Multiplying them orders
suggestions sensibly but drags the magnitude far below the real hit rate: on the
first benchmark run it read about 0.32 for suggestions that turned out correct
every single time, an expected calibration error of 0.50. A number like that is
worse than no number, because a responder reads "32%" and discounts a check that
is almost certainly worth running.

This module fits a one-dimensional monotone map from raw score to probability,
using **Platt scaling** - a two-parameter logistic fit. Two parameters rather
than something more flexible (isotonic regression, say) because the corpus is
tiny and anything with more freedom would memorise it.

Training data comes from **leave-one-out over the retrieval pool only**. Each
pool incident is held out in turn, one of its reference checks is removed to
simulate an investigation that skipped it, and the validator runs against the
remaining pool. Whether each resulting suggestion was the removed check gives
the label. The held-out split is never touched during fitting, so the
calibration error measured on it is an honest out-of-sample number.

Three details that matter with this little data:

- **Platt's target smoothing.** Labels are fitted towards `(N+1)/(N+2)` and
  `1/(N+2)` instead of hard 1 and 0. Without it, a perfectly separable sample
  pushes the fit towards infinite slope and the model claims 0.999 confidence
  on the strength of a few dozen examples.
- **The input is standardized first.** Raw scores occupy a narrow band (roughly
  0.2 to 0.5), and an L2 penalty applied to an un-standardized input of that
  scale overwhelms the data term: the first attempt here converged to a slope of
  2.74 and predicted 0.65 for a bucket whose observed hit rate was 1.00.
  Standardizing makes the penalty mean the same thing regardless of how the raw
  score happens to be scaled, so changing the score formula later cannot quietly
  under- or over-regularize the fit.
- **The penalty strength is cross-validated, not chosen by hand.** A hand-picked
  constant tuned until the numbers looked good would be fitting the benchmark.

The fit is plain gradient descent rather than Newton/IRLS: two parameters over a
few hundred samples converges in milliseconds, and it keeps the module free of a
numerical dependency.
"""

import math
import random
from typing import List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

# Penalty strengths tried during cross-validation, on standardized inputs.
L2_GRID = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0)
CV_FOLDS = 5
DEFAULT_ITERATIONS = 3000
DEFAULT_LEARNING_RATE = 0.5
# Keeps log-loss finite when a fold predicts an outcome with near-certainty.
_LOG_LOSS_EPSILON = 1e-12


class CalibrationModel(BaseModel):
    """A fitted logistic map from raw evidence score to probability."""

    slope: float = 1.0
    intercept: float = 0.0
    mean: float = Field(default=0.0, description="Training mean, used to standardize the input.")
    std: float = Field(default=1.0, description="Training standard deviation of the input.")
    l2: float = Field(default=0.0, description="Penalty strength chosen by cross-validation.")
    samples: int = Field(default=0, description="Leave-one-out examples the fit was built from.")
    positives: int = Field(default=0, description="How many of those were genuinely missing checks.")
    fitted: bool = False

    def apply(self, raw_confidence: float) -> float:
        """Map a raw score to a probability. Identity until the model is fitted."""
        if not self.fitted:
            return raw_confidence
        standardized = (raw_confidence - self.mean) / self.std
        return _sigmoid(self.slope * standardized + self.intercept)

    def describe(self) -> str:
        if not self.fitted:
            return "uncalibrated (raw evidence score passed through unchanged)"
        return (
            f"platt(slope={self.slope:.2f}, intercept={self.intercept:.2f}, l2={self.l2:g}) "
            f"fitted on {self.samples} leave-one-out samples, {self.positives} positive"
        )


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    # Rearranged for negative inputs so exp() cannot overflow.
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _descend(
    standardized: Sequence[float],
    targets: Sequence[float],
    l2: float,
    iterations: int,
    learning_rate: float,
) -> Tuple[float, float]:
    """Gradient descent for a two-parameter logistic fit. The intercept is unpenalized."""
    slope, intercept = 0.0, 0.0
    n = len(standardized)
    for _ in range(iterations):
        slope_gradient = 0.0
        intercept_gradient = 0.0
        for value, target in zip(standardized, targets):
            error = _sigmoid(slope * value + intercept) - target
            slope_gradient += error * value
            intercept_gradient += error
        slope -= learning_rate * (slope_gradient / n + l2 * slope)
        intercept -= learning_rate * (intercept_gradient / n)
    return slope, intercept


def _smoothed_targets(labels: Sequence[bool]) -> List[float]:
    """Platt's target smoothing: fit towards (N+1)/(N+2) and 1/(N+2), not 1 and 0."""
    positives = sum(1 for label in labels if label)
    negatives = len(labels) - positives
    positive_target = (positives + 1.0) / (positives + 2.0)
    negative_target = 1.0 / (negatives + 2.0)
    return [positive_target if label else negative_target for label in labels]


def _log_loss(predictions: Sequence[float], labels: Sequence[bool]) -> float:
    total = 0.0
    for prediction, label in zip(predictions, labels):
        clamped = min(1.0 - _LOG_LOSS_EPSILON, max(_LOG_LOSS_EPSILON, prediction))
        total -= math.log(clamped) if label else math.log(1.0 - clamped)
    return total / len(predictions)


def _select_l2(
    standardized: Sequence[float],
    labels: Sequence[bool],
    iterations: int,
    learning_rate: float,
    folds: int = CV_FOLDS,
) -> float:
    """Pick the penalty strength by k-fold cross-validated log loss.

    Log loss is scored against the true labels, not the smoothed targets: the
    question is which penalty predicts unseen outcomes best, not which one best
    reproduces the smoothing.
    """
    n = len(standardized)
    folds = min(folds, n)
    if folds < 2:
        return L2_GRID[len(L2_GRID) // 2]

    # Fixed seed: the same corpus must always produce the same model.
    order = list(range(n))
    random.Random(0).shuffle(order)
    assignment = {index: position % folds for position, index in enumerate(order)}

    best_l2, best_loss = L2_GRID[0], float("inf")
    for l2 in L2_GRID:
        losses = []
        for fold in range(folds):
            train = [i for i in range(n) if assignment[i] != fold]
            test = [i for i in range(n) if assignment[i] == fold]
            train_labels = [labels[i] for i in train]
            if not test or len(set(train_labels)) < 2:
                continue
            slope, intercept = _descend(
                [standardized[i] for i in train],
                _smoothed_targets(train_labels),
                l2,
                iterations,
                learning_rate,
            )
            predictions = [_sigmoid(slope * standardized[i] + intercept) for i in test]
            losses.append(_log_loss(predictions, [labels[i] for i in test]))
        if losses:
            mean_loss = sum(losses) / len(losses)
            if mean_loss < best_loss:
                best_loss, best_l2 = mean_loss, l2
    return best_l2


def fit_platt(
    raw_scores: Sequence[float],
    labels: Sequence[bool],
    l2: Optional[float] = None,
    iterations: int = DEFAULT_ITERATIONS,
    learning_rate: float = DEFAULT_LEARNING_RATE,
) -> CalibrationModel:
    """Fit `p = sigmoid(slope * z(raw) + intercept)` on standardized inputs.

    `l2` is chosen by cross-validation unless one is passed explicitly.

    Returns an unfitted (identity) model when there is not enough signal to fit:
    fewer than two samples, only one class present, or no variation in the raw
    score. Claiming calibration on any of those would just restate the training
    prior while looking like a measurement.
    """
    n = len(raw_scores)
    positives = sum(1 for label in labels if label)
    if n < 2 or positives == 0 or positives == n:
        return CalibrationModel(samples=n, positives=positives, fitted=False)

    mean = sum(raw_scores) / n
    variance = sum((raw - mean) ** 2 for raw in raw_scores) / n
    std = math.sqrt(variance)
    if std == 0:
        return CalibrationModel(samples=n, positives=positives, fitted=False)

    standardized = [(raw - mean) / std for raw in raw_scores]
    chosen_l2 = l2 if l2 is not None else _select_l2(standardized, labels, iterations, learning_rate)
    slope, intercept = _descend(
        standardized, _smoothed_targets(labels), chosen_l2, iterations, learning_rate
    )

    return CalibrationModel(
        slope=slope,
        intercept=intercept,
        mean=mean,
        std=std,
        l2=chosen_l2,
        samples=n,
        positives=positives,
        fitted=True,
    )


def build_calibration_samples(
    pool: Sequence["IncidentRecord"],  # noqa: F821 - imported lazily below
    retrieval_policy: Optional["RetrievalPolicy"] = None,  # noqa: F821
    signature_level: Optional["SignatureLevel"] = None,  # noqa: F821
) -> List[Tuple[float, bool]]:
    """Generate (raw score, was-correct) pairs by leave-one-out over the pool.

    For every pool incident and every check in its reference path, drop that one
    check, run retrieval against the *rest* of the pool, and label each
    suggestion by whether it is the dropped check.

    Suggestions are collected with the support filters switched off on purpose.
    The filters exist to remove weakly supported checks, but the calibration map
    is precisely what should learn how much a weakly supported check is worth -
    so it needs to see the low-scoring, usually-wrong examples too.
    """
    from holmes.core.investigation_path.retrieval import RetrievalPolicy, retrieve
    from holmes.core.investigation_path.schema import SignatureLevel
    from holmes.core.investigation_path.validator import SuggestionPolicy, validate_path

    retrieval_policy = retrieval_policy or RetrievalPolicy()
    level = signature_level or retrieval_policy.signature_level
    unfiltered = SuggestionPolicy(
        min_support=1,
        min_support_ratio=0.0,
        min_confidence=0.0,
        max_suggestions=1000,
        signature_level=level,
    )

    samples: List[Tuple[float, bool]] = []
    for index, incident in enumerate(pool):
        others = [other for position, other in enumerate(pool) if position != index]
        reference_signatures = {step.signature(level) for step in incident.reference_path}

        for step in incident.reference_path:
            dropped = step.signature(level)
            observed = sorted(reference_signatures - {dropped})
            # Same entity-transfer rule as inference, or the calibration map
            # would be fitted on a suggestion distribution that never occurs.
            known_entities = {
                other.entity.name
                for other in incident.reference_path
                if other.entity.name and other is not step
            }

            retrieval = retrieve(incident.symptoms, others, retrieval_policy)
            if retrieval.abstained:
                continue
            report = validate_path(
                observed,
                retrieval,
                unfiltered,
                subject=incident.subject,
                known_entities=known_entities,
            )
            for suggestion in report.suggestions:
                samples.append((suggestion.raw_confidence, suggestion.signature == dropped))
    return samples


def fit_calibration(
    pool: Sequence["IncidentRecord"],  # noqa: F821
    retrieval_policy: Optional["RetrievalPolicy"] = None,  # noqa: F821
    signature_level: Optional["SignatureLevel"] = None,  # noqa: F821
) -> CalibrationModel:
    """Fit a calibration model from the retrieval pool alone."""
    samples = build_calibration_samples(pool, retrieval_policy, signature_level)
    if not samples:
        return CalibrationModel(fitted=False)
    raw_scores = [raw for raw, _ in samples]
    labels = [label for _, label in samples]
    return fit_platt(raw_scores, labels)
