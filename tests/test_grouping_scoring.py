"""Unit tests for the alert-grouping scoring metrics (no LLM, no network).

Hand-checked values verify the ported pairwise / ARI / coverage math and the
cost/speed helpers used by the grouping evals.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "tests/llm/fixtures/test_ask_holmes/shared/grouping"
    ),
)

from scoring import (  # noqa: E402
    adjusted_rand_index,
    cost_speed,
    coverage,
    incidents_to_partition,
    pairwise_prf,
    score_all,
)


def test_perfect_match():
    truth = {"a": "g1", "b": "g1", "c": "g2", "d": "g2"}
    predicted = {"a": "x", "b": "x", "c": "y", "d": "y"}  # labels differ, structure same
    prf = pairwise_prf(predicted, truth)
    assert prf["precision"] == 1.0
    assert prf["recall"] == 1.0
    assert prf["f1"] == 1.0
    assert adjusted_rand_index(predicted, truth) == 1.0
    assert coverage(predicted, truth) == 1.0


def test_over_grouping_everything_merged():
    # Truth: two groups of 2. Predicted: all four in one incident.
    truth = {"a": "g1", "b": "g1", "c": "g2", "d": "g2"}
    predicted = {"a": "one", "b": "one", "c": "one", "d": "one"}
    prf = pairwise_prf(predicted, truth)
    # truth same-pairs = 2 (ab, cd). predicted same-pairs = C(4,2)=6. tp=2.
    assert prf["tp"] == 2
    assert prf["fp"] == 4  # 6 predicted - 2 true
    assert prf["fn"] == 0
    assert prf["recall"] == 1.0
    assert prf["precision"] == pytest.approx(2 / 6)


def test_under_grouping_all_separate():
    # Truth: one group of 3. Predicted: each alert in its own incident.
    truth = {"a": "g1", "b": "g1", "c": "g1"}
    predicted = {"a": "1", "b": "2", "c": "3"}
    prf = pairwise_prf(predicted, truth)
    # no predicted pairs -> tp=0, fp=0; truth pairs = C(3,2)=3 -> fn=3
    assert prf["tp"] == 0
    assert prf["fp"] == 0
    assert prf["fn"] == 3
    assert prf["precision"] == 1.0  # vacuously (no positives predicted)
    assert prf["recall"] == 0.0
    # ARI of all-singletons vs a real cluster is 0 (chance level)
    assert adjusted_rand_index(predicted, truth) == pytest.approx(0.0, abs=1e-9)


def test_coverage_partial():
    truth = {"a": "g1", "b": "g1", "c": "g2", "d": "g2"}
    predicted = {"a": "x", "b": "x"}  # only 2 of 4 alerts placed
    assert coverage(predicted, truth) == 0.5


def test_score_all_keys():
    truth = {"a": "g1", "b": "g2"}
    predicted = {"a": "x", "b": "y"}
    s = score_all(predicted, truth)
    assert set(s) == {"pairwise_precision", "pairwise_recall", "pairwise_f1", "ari", "coverage"}


def test_incidents_to_partition():
    incidents = [
        {"id": "INC-1", "related_alerts": ["a", "b"]},
        {"id": "INC-2", "related_alerts": ["c"]},
    ]
    assert incidents_to_partition(incidents) == {"a": "INC-1", "b": "INC-1", "c": "INC-2"}


def test_cost_speed():
    cs = cost_speed(total_cost_usd=1.0, total_seconds=50.0, n_alerts=10)
    assert cs["cost_per_alert_usd"] == pytest.approx(0.1)
    assert cs["seconds_per_alert"] == pytest.approx(5.0)
    # never divides by zero
    cs0 = cost_speed(0.0, 0.0, 0)
    assert cs0["cost_per_alert_usd"] == 0.0
