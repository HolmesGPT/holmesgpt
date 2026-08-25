"""Grouping-quality + cost/speed scoring for alert-grouping evals.

The clustering metrics (pairwise precision/recall/F1, Adjusted Rand Index,
coverage) are a faithful port of the temporary `triager` project's
`src/evaluation/metrics.py`, kept here so the grouping evals keep working after
triager is retired. They are pure functions over partitions and depend on
nothing else in the repo.

A "partition" is a mapping alert_id -> group label (the incident an alert was
placed in). Ground truth is the same shape (alert_id -> correct incident key).
Only alerts present in BOTH partitions are scored, so a run that drops alerts is
penalised through `coverage`, and grouping quality is measured on what it did
place.

Time is handled entirely through the labels: two episodes of the same resource
on different days are simply given different ground-truth keys, so grouping them
together shows up as false-positive pairs. No special temporal logic is needed
in the scorer.
"""

from __future__ import annotations

from collections import Counter
from math import comb
from typing import Mapping

Partition = Mapping[str, str]


def _common(predicted: Partition, truth: Partition) -> list[str]:
    return [a for a in truth if a in predicted]


def _contingency(predicted: Partition, truth: Partition):
    """Contingency table over alerts present in both partitions.

    Returns (table, truth_sizes, pred_sizes, n) where table maps
    (pred_label, truth_label) -> count.
    """
    common = _common(predicted, truth)
    table: Counter = Counter()
    truth_sizes: Counter = Counter()
    pred_sizes: Counter = Counter()
    for a in common:
        table[(predicted[a], truth[a])] += 1
        truth_sizes[truth[a]] += 1
        pred_sizes[predicted[a]] += 1
    return table, truth_sizes, pred_sizes, len(common)


def pairwise_prf(predicted: Partition, truth: Partition) -> dict[str, float]:
    """Pairwise precision / recall / F1 over alerts in both partitions.

    A "positive" is a pair of alerts placed in the same incident. Precision is
    how many predicted same-incident pairs are truly together; recall is how
    many truly-together pairs were caught.
    """
    table, truth_sizes, pred_sizes, n = _contingency(predicted, truth)
    if n < 2:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}
    tp = sum(comb(c, 2) for c in table.values())
    same_truth = sum(comb(c, 2) for c in truth_sizes.values())
    same_pred = sum(comb(c, 2) for c in pred_sizes.values())
    fp = same_pred - tp
    fn = same_truth - tp
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def adjusted_rand_index(predicted: Partition, truth: Partition) -> float:
    """ARI in [~0, 1]; chance-adjusted agreement between the two partitions."""
    table, truth_sizes, pred_sizes, n = _contingency(predicted, truth)
    if n < 2:
        return 1.0
    index = sum(comb(c, 2) for c in table.values())
    sum_pred = sum(comb(c, 2) for c in pred_sizes.values())
    sum_truth = sum(comb(c, 2) for c in truth_sizes.values())
    expected = (sum_pred * sum_truth) / comb(n, 2)
    max_index = 0.5 * (sum_pred + sum_truth)
    if max_index == expected:
        return 1.0
    return (index - expected) / (max_index - expected)


def coverage(predicted: Partition, truth: Partition) -> float:
    """Fraction of ground-truth alerts that ended up in any predicted incident."""
    if not truth:
        return 1.0
    return len(_common(predicted, truth)) / len(truth)


def score_all(predicted: Partition, truth: Partition) -> dict[str, float]:
    """Full grouping-quality scorecard for a run."""
    prf = pairwise_prf(predicted, truth)
    return {
        "pairwise_precision": prf["precision"],
        "pairwise_recall": prf["recall"],
        "pairwise_f1": prf["f1"],
        "ari": adjusted_rand_index(predicted, truth),
        "coverage": coverage(predicted, truth),
    }


# ---- cost & speed ----------------------------------------------------------


def cost_speed(total_cost_usd: float, total_seconds: float, n_alerts: int) -> dict[str, float]:
    """Total and per-alert cost/speed. ROB-517 DoD #3 (cost + speed)."""
    n = max(n_alerts, 1)
    return {
        "total_cost_usd": total_cost_usd,
        "cost_per_alert_usd": total_cost_usd / n,
        "total_seconds": total_seconds,
        "seconds_per_alert": total_seconds / n,
    }


def incidents_to_partition(incidents: list[dict]) -> dict[str, str]:
    """Turn a list of incident dicts (each with id + related_alerts) into an
    alert_id -> incident_id partition for scoring."""
    partition: dict[str, str] = {}
    for inc in incidents:
        inc_id = str(inc.get("id"))
        for alert_id in inc.get("related_alerts", []) or []:
            partition[str(alert_id)] = inc_id
    return partition


def format_summary(scores: dict[str, float], label: str = "") -> str:
    """One-line-per-metric human summary of score_all() + cost_speed()."""
    lines = [f"grouping run summary {label}".rstrip()]
    if "pairwise_f1" in scores:
        lines.append(
            "  P/R/F1:   "
            f"{scores.get('pairwise_precision', float('nan')):.3f} / "
            f"{scores.get('pairwise_recall', float('nan')):.3f} / "
            f"{scores.get('pairwise_f1', float('nan')):.3f}"
        )
        lines.append(f"  ARI:      {scores.get('ari', float('nan')):.3f}")
        lines.append(f"  coverage: {scores.get('coverage', float('nan')):.3f}")
    if "total_cost_usd" in scores:
        lines.append(
            f"  cost:     ${scores['total_cost_usd']:.4f} total | "
            f"${scores.get('cost_per_alert_usd', 0):.4f}/alert"
        )
    if "total_seconds" in scores:
        lines.append(
            f"  speed:    {scores['total_seconds']:.1f}s total | "
            f"{scores.get('seconds_per_alert', 0):.1f}s/alert"
        )
    return "\n".join(lines)
