"""Suggestions: what gets surfaced, with what provenance, and how it reads."""

import pytest

from holmes.core.investigation_path.calibration_model import CalibrationModel
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

# Turns off the support-ratio filter, for tests that need to see the minority
# suggestions the default policy is designed to remove.
PERMISSIVE = SuggestionPolicy(min_support_ratio=0.0)


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
        report = validate_path([], retrieval, PERMISSIVE)
        by_signature = {s.signature: s for s in report.suggestions}
        assert by_signature["topology:endpoints:redis"].support == 2
        assert by_signature["topology:service:redis"].support == 1
        assert by_signature["topology:endpoints:redis"].out_of == 2

    def test_more_important_and_better_supported_checks_come_first(self, retrieval):
        report = validate_path([], retrieval, PERMISSIVE)
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
        report = validate_path([], retrieval, PERMISSIVE)
        by_signature = {s.signature: s for s in report.suggestions}
        assert by_signature["topology:endpoints:redis"].confidence > (
            by_signature["topology:service:redis"].confidence
        )

    def test_raising_min_support_drops_minority_checks(self, retrieval):
        report = validate_path([], retrieval, SuggestionPolicy(min_support=2))
        assert "topology:service:redis" not in [s.signature for s in report.suggestions]
        assert "topology:endpoints:redis" in [s.signature for s in report.suggestions]


class TestSupportRatioFilter:
    """A check only one incident out of several ran is that incident's own
    circumstance, not a property of the root cause. Suggesting it anyway was the
    single largest source of false positives on the benchmark."""

    def test_minority_checks_are_dropped_by_default(self, retrieval):
        signatures = [s.signature for s in validate_path([], retrieval).suggestions]
        assert "topology:service:redis" not in signatures
        assert "metrics:metric:redis_memory_used_bytes" not in signatures

    def test_unanimous_checks_survive(self, retrieval):
        signatures = [s.signature for s in validate_path([], retrieval).suggestions]
        assert "topology:endpoints:redis" in signatures
        assert "logs:pod:<subject>" in signatures

    def test_the_ratio_is_measured_against_the_match_count(self):
        # Two of three is 0.67, which clears the default 0.6 floor.
        pool = [
            incident("INC-001", SYMPTOMS, [step(QueryIntent.LOGS, "pod", "api")]),
            incident("INC-002", SYMPTOMS, [step(QueryIntent.LOGS, "pod", "api")]),
            incident("INC-003", SYMPTOMS, [step(QueryIntent.EVENTS, "pod", "api")]),
        ]
        report = validate_path([], retrieve(SYMPTOMS, pool))
        signatures = [s.signature for s in report.suggestions]
        assert "logs:pod:api" in signatures
        assert "events:pod:api" not in signatures

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


class TestEntityTransfer:
    """Two incidents can share symptoms and a root cause while depending on
    completely different services. Suggesting the other incident's dependency by
    name sends a responder to something that does not exist here."""

    @pytest.fixture
    def redis_retrieval(self):
        path = [
            step(QueryIntent.LOGS, "pod", "<subject>"),
            step(QueryIntent.TOPOLOGY, "service", "redis"),
            step(QueryIntent.TOPOLOGY, "endpoints", "redis"),
            step(QueryIntent.METRICS, "metric", "redis_connected_clients"),
            step(QueryIntent.RESOURCE_USAGE, "metric", "container_memory_working_set_bytes"),
        ]
        pool = [incident("INC-001", SYMPTOMS, path), incident("INC-002", SYMPTOMS, path)]
        return retrieve(SYMPTOMS, pool)

    def test_an_object_the_investigation_never_saw_is_not_suggested(self, redis_retrieval):
        report = validate_path([], redis_retrieval, known_entities={"queue-worker"})
        signatures = [s.signature for s in report.suggestions]
        assert "topology:service:redis" not in signatures
        assert "topology:endpoints:redis" not in signatures

    def test_an_object_the_investigation_did_see_is_still_suggested(self, redis_retrieval):
        report = validate_path([], redis_retrieval, known_entities={"redis"})
        assert "topology:service:redis" in [s.signature for s in report.suggestions]

    def test_a_metric_named_after_a_foreign_object_is_not_suggested(self, redis_retrieval):
        """`redis_connected_clients` is a metric, but only where Redis exists."""
        report = validate_path([], redis_retrieval, known_entities={"queue-worker"})
        assert "metrics:metric:redis_connected_clients" not in [
            s.signature for s in report.suggestions
        ]

    def test_a_genuinely_generic_metric_still_transfers(self, redis_retrieval):
        report = validate_path([], redis_retrieval, known_entities={"queue-worker"})
        assert "resource_usage:metric:container_memory_working_set_bytes" in [
            s.signature for s in report.suggestions
        ]

    def test_subject_relative_checks_always_transfer(self, redis_retrieval):
        report = validate_path([], redis_retrieval, known_entities={"queue-worker"})
        assert "logs:pod:<subject>" in [s.signature for s in report.suggestions]

    def test_token_matching_does_not_reject_on_a_substring(self):
        """A pod named `mem` must not veto `node_memory_MemAvailable_bytes`."""
        path = [
            step(QueryIntent.DESCRIBE, "pod", "mem"),
            step(QueryIntent.METRICS, "metric", "node_memory_MemAvailable_bytes"),
        ]
        pool = [incident("INC-001", SYMPTOMS, path), incident("INC-002", SYMPTOMS, path)]
        report = validate_path([], retrieve(SYMPTOMS, pool), known_entities={"other"})
        signatures = [s.signature for s in report.suggestions]
        assert "metrics:metric:node_memory_MemAvailable_bytes" in signatures
        assert "describe:pod:mem" not in signatures

    def test_callers_that_cannot_supply_known_entities_are_not_filtered(self, redis_retrieval):
        report = validate_path([], redis_retrieval, known_entities=None)
        assert "topology:service:redis" in [s.signature for s in report.suggestions]


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

    def test_an_uncalibrated_score_is_never_shown_as_a_percentage(self, retrieval):
        """The raw score is a product of terms below 1, so it reads far below the
        real hit rate. Printing it teaches responders to ignore the block."""
        markdown = validate_path([], retrieval, subject="orders-service").to_markdown()
        assert "Confidence" not in markdown

    def test_a_calibrated_score_is_shown(self, retrieval):
        """It is computed either way; not showing it wastes the calibration."""
        report = validate_path(
            [],
            retrieval,
            subject="orders-service",
            calibration=CalibrationModel(slope=2.2, intercept=0.35, fitted=True),
        )
        assert "Confidence" in report.to_markdown()
        assert all(s.calibrated for s in report.suggestions)

    def test_an_unfitted_model_counts_as_uncalibrated(self, retrieval):
        """`fit_calibration` returns an unfitted model when the pool is too small,
        and that must not silently start printing percentages."""
        report = validate_path(
            [],
            retrieval,
            subject="orders-service",
            calibration=CalibrationModel(fitted=False),
        )
        assert not any(s.calibrated for s in report.suggestions)
        assert "Confidence" not in report.to_markdown()

    def test_the_shown_percentage_is_the_calibrated_one(self, retrieval):
        report = validate_path(
            [],
            retrieval,
            subject="orders-service",
            calibration=CalibrationModel(slope=2.2, intercept=0.35, fitted=True),
        )
        top = report.suggestions[0]
        assert top.confidence != top.raw_confidence
        assert f"Confidence {top.confidence:.0%}" in report.to_markdown()

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
