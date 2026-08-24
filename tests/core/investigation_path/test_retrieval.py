"""Retrieval: symptom ranking, root-cause separation, and every abstention path."""

import pytest

from holmes.core.investigation_path.retrieval import (
    AbstainReason,
    RetrievalPolicy,
    retrieve,
    symptom_similarity,
)
from holmes.core.investigation_path.schema import IncidentRecord, RootCause


def incident(incident_id, symptoms, root_cause="dependency_unreachable"):
    return IncidentRecord(
        incident_id=incident_id,
        occurred_at="2026-01-01",
        source_type="prometheus",
        title=incident_id,
        symptoms=symptoms,
        root_cause=RootCause(label=root_cause, summary="summary"),
        validated_by="test",
        validated_at="2026-01-01",
    )


class TestSymptomSimilarity:
    def test_identical_sets_score_one(self):
        assert symptom_similarity(["redis", "crash"], ["crash", "redis"]) == 1.0

    def test_disjoint_sets_score_zero(self):
        assert symptom_similarity(["redis"], ["postgres"]) == 0.0

    def test_empty_input_scores_zero(self):
        assert symptom_similarity([], ["redis"]) == 0.0

    def test_partial_overlap(self):
        assert symptom_similarity(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)

    def test_matching_ignores_case(self):
        assert symptom_similarity(["Redis"], ["redis"]) == 1.0


class TestAbstention:
    def test_empty_corpus_abstains_with_no_candidates(self):
        result = retrieve(["redis", "crash"], [])
        assert result.abstained
        assert result.abstain_reason == AbstainReason.NO_CANDIDATES

    def test_unrelated_corpus_abstains_with_no_candidates(self):
        result = retrieve(["tls", "certificate"], [incident("A", ["redis", "crash", "pod"])])
        assert result.abstain_reason == AbstainReason.NO_CANDIDATES

    def test_weak_overlap_abstains_with_low_similarity(self):
        pool = [incident("A", ["redis", "crash", "pod", "restart", "timeout", "payment"])]
        result = retrieve(["redis", "tls", "certificate", "expired", "ingress"], pool)
        assert result.abstain_reason == AbstainReason.LOW_SIMILARITY

    def test_a_single_matching_incident_abstains(self):
        pool = [incident("A", ["redis", "crash", "pod"])]
        result = retrieve(["redis", "crash", "pod"], pool)
        assert result.abstain_reason == AbstainReason.INSUFFICIENT_SUPPORT

    def test_disagreeing_root_causes_abstain(self):
        pool = [
            incident("A", ["redis", "crash", "pod"], root_cause="dependency_unreachable"),
            incident("B", ["redis", "crash", "pod"], root_cause="oom_kill"),
            incident("C", ["redis", "crash", "pod"], root_cause="config_regression"),
        ]
        result = retrieve(["redis", "crash", "pod"], pool)
        assert result.abstain_reason == AbstainReason.ROOT_CAUSE_DISAGREEMENT
        assert result.root_cause_agreement == pytest.approx(1 / 3)

    def test_rejected_candidates_are_still_reported(self):
        pool = [incident("A", ["redis", "crash", "pod"])]
        result = retrieve(["redis", "crash", "pod"], pool)
        assert [c.incident.incident_id for c in result.candidates] == ["A"]
        assert result.matches == []

    def test_every_abstention_has_a_readable_reason(self):
        for reason in AbstainReason:
            from holmes.core.investigation_path.retrieval import RetrievalResult

            result = RetrievalResult(abstained=True, abstain_reason=reason)
            assert result.explain_abstention()


class TestAnswering:
    @pytest.fixture
    def pool(self):
        return [
            incident("A", ["redis", "crash", "pod", "restart"]),
            incident("B", ["redis", "crash", "pod", "timeout"]),
            incident("C", ["disk", "node", "evicted"], root_cause="node_disk_pressure"),
        ]

    def test_two_agreeing_incidents_are_enough_to_answer(self, pool):
        result = retrieve(["redis", "crash", "pod", "restart"], pool)
        assert not result.abstained
        assert [m.incident.incident_id for m in result.matches] == ["A", "B"]
        assert result.modal_root_cause == "dependency_unreachable"

    def test_unrelated_incidents_are_not_matched(self, pool):
        result = retrieve(["redis", "crash", "pod", "restart"], pool)
        assert "C" not in [m.incident.incident_id for m in result.matches]

    def test_matches_are_ranked_by_similarity(self, pool):
        result = retrieve(["redis", "crash", "pod", "restart"], pool)
        scores = [m.symptom_similarity for m in result.matches]
        assert scores == sorted(scores, reverse=True)

    def test_minority_root_causes_are_dropped_from_matches(self):
        pool = [
            incident("A", ["redis", "crash", "pod"], root_cause="dependency_unreachable"),
            incident("B", ["redis", "crash", "pod"], root_cause="dependency_unreachable"),
            incident("C", ["redis", "crash", "pod"], root_cause="oom_kill"),
        ]
        result = retrieve(["redis", "crash", "pod"], pool)
        assert [m.incident.incident_id for m in result.matches] == ["A", "B"]
        assert len(result.candidates) == 3

    def test_confidence_rises_with_more_agreeing_incidents(self):
        symptoms = ["redis", "crash", "pod"]
        two = retrieve(symptoms, [incident("A", symptoms), incident("B", symptoms)])
        three = retrieve(
            symptoms, [incident("A", symptoms), incident("B", symptoms), incident("C", symptoms)]
        )
        assert three.confidence > two.confidence

    def test_confidence_stays_within_zero_and_one(self):
        symptoms = ["redis", "crash", "pod"]
        result = retrieve(symptoms, [incident(str(i), symptoms) for i in range(5)])
        assert 0.0 <= result.confidence <= 1.0

    def test_candidate_count_is_capped(self):
        symptoms = ["redis", "crash", "pod"]
        pool = [incident(f"INC-{i}", symptoms) for i in range(10)]
        result = retrieve(symptoms, pool, RetrievalPolicy(max_candidates=3))
        assert len(result.candidates) == 3

    def test_raising_the_similarity_floor_forces_abstention(self):
        pool = [incident("A", ["redis", "crash", "pod"]), incident("B", ["redis", "crash", "pod"])]
        strict = retrieve(["redis", "crash"], pool, RetrievalPolicy(min_symptom_similarity=0.95))
        assert strict.abstained
