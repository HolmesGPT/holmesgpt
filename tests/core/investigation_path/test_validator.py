"""Suggestions: what gets surfaced, with what provenance, and how it reads."""

import pytest

from holmes.core.investigation_path.retrieval import RetrievalPolicy, retrieve
from holmes.core.investigation_path.schema import (
    EntityRef,
    IncidentRecord,
    QueryIntent,
    ReferenceStep,
    RootCause,
)
from holmes.core.investigation_path.validator import SuggestionPolicy, validate_path


def step(intent, kind, name, weight=1.0, rationale="because it matters"):
    return ReferenceStep(
        intent=intent,
        entity=EntityRef(kind=kind, name=name),
        weight=weight,
        rationale=rationale,
    )


def incident(incident_id, symptoms, reference_path, root_cause="dependency_unreachable"):
    return IncidentRecord(
        incident_id=incident_id,
        occurred_at="2026-01-01",
        source_type="prometheus",
        title=f"Title for {incident_id}",
        symptoms=symptoms,
        root_cause=RootCause(label=root_cause, summary="summary"),
        reference_path=reference_path,
        validated_by="test",
        validated_at="2026-01-01",
    )


SYMPTOMS = ["redis", "crash", "pod", "restart"]


@pytest.fixture
def retrieval():
    pool = [
        incident(
            "INC-001",
            SYMPTOMS,
            [
                step(QueryIntent.LOGS, "pod", "<subject>"),
                step(QueryIntent.TOPOLOGY, "endpoints", "redis", weight=1.0),
                step(QueryIntent.TOPOLOGY, "service", "redis", weight=0.9),
            ],
        ),
        incident(
            "INC-002",
            SYMPTOMS,
            [
                step(QueryIntent.LOGS, "pod", "<subject>"),
                step(QueryIntent.TOPOLOGY, "endpoints", "redis", weight=1.0),
                step(QueryIntent.METRICS, "metric", "redis_memory_used_bytes", weight=0.3),
            ],
        ),
    ]
    return retrieve(SYMPTOMS, pool)


class TestSuggestions:
    def test_checks_already_performed_are_not_suggested(self, retrieval):
        report = validate_path(["logs:pod:<subject>", "topology:endpoints:redis"], retrieval)
        assert "logs:pod:<subject>" not in [s.signature for s in report.suggestions]
        assert "topology:endpoints:redis" not in [s.signature for s in report.suggestions]

    def test_skipped_checks_are_suggested(self, retrieval):
        report = validate_path(["logs:pod:<subject>"], retrieval)
        assert "topology:endpoints:redis" in [s.signature for s in report.suggestions]

    def test_support_counts_how_many_incidents_ran_the_check(self, retrieval):
        report = validate_path([], retrieval)
        by_signature = {s.signature: s for s in report.suggestions}
        assert by_signature["topology:endpoints:redis"].support == 2
        assert by_signature["topology:service:redis"].support == 1
        assert by_signature["topology:endpoints:redis"].out_of == 2

    def test_more_important_and_better_supported_checks_come_first(self, retrieval):
        report = validate_path([], retrieval)
        ranking = [s.weight * s.support for s in report.suggestions]
        assert ranking == sorted(ranking, reverse=True)
        assert report.suggestions[-1].signature == "metrics:metric:redis_memory_used_bytes"

    def test_every_suggestion_carries_provenance(self, retrieval):
        report = validate_path([], retrieval)
        for suggestion in report.suggestions:
            assert suggestion.provenance
            for source in suggestion.provenance:
                assert source.incident_id
                assert source.occurred_at
                assert source.root_cause_label

    def test_every_suggestion_carries_a_rationale(self, retrieval):
        report = validate_path([], retrieval)
        assert all(s.rationale for s in report.suggestions)

    def test_confidence_scales_with_support(self, retrieval):
        report = validate_path([], retrieval)
        by_signature = {s.signature: s for s in report.suggestions}
        assert by_signature["topology:endpoints:redis"].confidence > (
            by_signature["topology:service:redis"].confidence
        )

    def test_raising_min_support_drops_minority_checks(self, retrieval):
        report = validate_path([], retrieval, SuggestionPolicy(min_support=2))
        assert "topology:service:redis" not in [s.signature for s in report.suggestions]
        assert "topology:endpoints:redis" in [s.signature for s in report.suggestions]

    def test_suggestion_count_is_capped(self, retrieval):
        report = validate_path([], retrieval, SuggestionPolicy(max_suggestions=1))
        assert len(report.suggestions) == 1

    def test_low_confidence_suggestions_are_dropped(self, retrieval):
        report = validate_path([], retrieval, SuggestionPolicy(min_confidence=0.99))
        assert report.suggestions == []

    def test_a_repeated_reference_step_cannot_inflate_its_own_support(self):
        pool = [
            incident("INC-001", SYMPTOMS, [step(QueryIntent.LOGS, "pod", "api")] * 3),
            incident("INC-002", SYMPTOMS, [step(QueryIntent.LOGS, "pod", "api")]),
        ]
        report = validate_path([], retrieve(SYMPTOMS, pool))
        assert report.suggestions[0].support == 2


class TestAbstainedReports:
    def test_an_abstained_retrieval_produces_no_suggestions(self):
        report = validate_path([], retrieve(["tls", "certificate"], []))
        assert report.abstained
        assert report.suggestions == []

    def test_an_abstained_report_explains_itself(self):
        report = validate_path([], retrieve(["tls", "certificate"], []))
        assert "no past incident resembled this one" in report.abstain_reason

    def test_an_abstained_report_renders_nothing(self):
        assert validate_path([], retrieve(["tls", "certificate"], [])).to_markdown() == ""


class TestRendering:
    def test_the_subject_token_is_rendered_as_the_real_workload(self, retrieval):
        report = validate_path([], retrieval, subject="orders-service")
        assert "<subject>" not in report.to_markdown()
        assert "orders-service" in report.to_markdown()

    def test_the_report_is_worded_as_advice_not_a_requirement(self, retrieval):
        markdown = validate_path([], retrieval, subject="orders-service").to_markdown()
        assert "not required steps" in markdown

    def test_the_report_names_its_sources(self, retrieval):
        markdown = validate_path([], retrieval, subject="orders-service").to_markdown()
        assert "INC-001" in markdown
        assert "2026-01-01" in markdown

    def test_the_report_names_the_shared_root_cause(self, retrieval):
        markdown = validate_path([], retrieval, subject="orders-service").to_markdown()
        assert "dependency_unreachable" in markdown

    def test_nothing_missing_renders_nothing(self, retrieval):
        report = validate_path(
            [
                "logs:pod:<subject>",
                "topology:endpoints:redis",
                "topology:service:redis",
                "metrics:metric:redis_memory_used_bytes",
            ],
            retrieval,
        )
        assert report.is_empty
        assert report.to_markdown() == ""
