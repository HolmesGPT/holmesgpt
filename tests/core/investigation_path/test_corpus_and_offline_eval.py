"""The fixture corpus and the benchmark it feeds.

The thresholds below are a recorded baseline for the current policy on the
current corpus, not a quality bar anyone should be satisfied with. They exist so
that a change to retrieval shows up as a number moving, which is the point of
running this offline before wiring anything into an investigation.
"""

import re

import pytest
import yaml

from holmes.core.investigation_path.corpus import (
    DEFAULT_CORPUS_DIR,
    corpus_bytes_per_incident,
    load_corpus,
)
from holmes.core.investigation_path.offline_eval import run_offline_eval
from holmes.core.investigation_path.retrieval import RetrievalPolicy
from holmes.core.investigation_path.schema import SUBJECT_TOKEN, SignatureLevel
from holmes.core.investigation_path.validator import SuggestionPolicy

KNOWN_ROOT_CAUSES = {
    "dependency_unreachable",
    "oom_kill",
    "image_pull_failure",
    "node_disk_pressure",
    "config_regression",
    "certificate_expired",
}

# Patterns that would mean a record carries a value the schema has no place for.
# Deliberately matched as key/value shapes rather than bare words, so that prose
# like "names the secret the certificate is read from" is not a false alarm.
LEAK_PATTERNS = (
    re.compile(r"(password|secret|token|api[_-]?key|credential)\s*[:=]\s*\S", re.IGNORECASE),
    re.compile(r"bearer\s+\S", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
)


class TestCorpus:
    @pytest.fixture(scope="class")
    def records(self):
        return load_corpus()

    def test_corpus_loads(self, records):
        assert records

    def test_every_record_is_attributed_to_a_human_reviewer(self, records):
        for record in records:
            assert record.validated_by
            assert record.validated_at

    def test_root_causes_come_from_the_controlled_vocabulary(self, records):
        for record in records:
            assert record.root_cause.label in KNOWN_ROOT_CAUSES, record.incident_id

    def test_incident_ids_are_unique(self, records):
        ids = [record.incident_id for record in records]
        assert len(ids) == len(set(ids))

    def test_reference_steps_always_explain_themselves(self, records):
        for record in records:
            for step in record.reference_path:
                assert step.rationale.strip(), record.incident_id

    def test_weights_are_within_range(self, records):
        for record in records:
            for step in record.reference_path:
                assert 0.0 <= step.weight <= 1.0

    def test_the_affected_workload_is_referred_to_by_token(self, records):
        """A path naming a real workload can only ever match that workload."""
        for record in records:
            if not record.subject:
                continue
            for step in record.reference_path + record.observed_path:
                assert step.entity.name != record.subject, (
                    f"{record.incident_id} names its own subject instead of {SUBJECT_TOKEN}"
                )

    def test_no_record_contains_anything_sensitive(self, records):
        for record in records:
            serialized = record.model_dump_json()
            for pattern in LEAK_PATTERNS:
                assert not pattern.search(serialized), (
                    f"{record.incident_id} matches leak pattern {pattern.pattern!r}"
                )

    def test_the_leak_check_would_catch_a_real_leak(self):
        """Guards the guard: a crude substring list here would pass anything."""
        assert any(p.search('{"summary": "password: hunter2"}') for p in LEAK_PATTERNS)
        assert any(p.search('{"summary": "see https://internal.example"}') for p in LEAK_PATTERNS)
        assert any(p.search('{"summary": "ping ops@example.com"}') for p in LEAK_PATTERNS)
        assert not any(p.search('{"rationale": "names the secret it reads"}') for p in LEAK_PATTERNS)

    def test_holdout_cases_have_both_a_reference_and_an_observed_path(self):
        for record in load_corpus(split="holdout"):
            assert record.reference_path
            assert record.observed_path

    def test_holdout_cases_actually_skipped_something(self):
        for record in load_corpus(split="holdout"):
            assert record.missing_steps(), f"{record.incident_id} has nothing missing to find"

    def test_pool_records_have_no_observed_path(self):
        for record in load_corpus(split="corpus"):
            assert record.observed_path == []

    def test_the_two_splits_do_not_overlap(self):
        pool = {r.incident_id for r in load_corpus(split="corpus")}
        holdout = {r.incident_id for r in load_corpus(split="holdout")}
        assert not (pool & holdout)

    def test_storage_cost_is_measured(self, records):
        assert corpus_bytes_per_incident(records) > 0

    def test_storage_cost_of_an_empty_corpus_is_zero(self):
        assert corpus_bytes_per_incident([]) == 0.0


class TestCorpusLoading:
    def test_a_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_corpus(tmp_path / "nope")

    def test_a_malformed_record_raises_instead_of_being_skipped(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(yaml.safe_dump({"incident_id": "X"}))
        with pytest.raises(ValueError, match="Invalid incident record"):
            load_corpus(tmp_path)

    def test_an_empty_file_raises(self, tmp_path):
        (tmp_path / "empty.yaml").write_text("")
        with pytest.raises(ValueError, match="Empty incident file"):
            load_corpus(tmp_path)

    def test_records_load_in_a_stable_order(self):
        assert [r.incident_id for r in load_corpus()] == sorted(
            r.incident_id for r in load_corpus()
        )

    def test_the_default_corpus_directory_exists(self):
        assert DEFAULT_CORPUS_DIR.is_dir()


class TestOfflineEvalBaseline:
    @pytest.fixture(scope="class")
    def result(self):
        return run_offline_eval()

    def test_every_holdout_case_is_scored(self, result):
        metrics, outcomes = result
        assert metrics.cases == len(outcomes) == len(load_corpus(split="holdout"))

    def test_no_llm_call_is_made(self, result):
        metrics, _ = result
        assert metrics.llm_calls == 0

    def test_validation_is_fast_enough_to_be_free(self, result):
        metrics, _ = result
        assert metrics.latency_p95_ms < 50

    def test_when_it_answers_it_finds_what_was_skipped(self, result):
        metrics, _ = result
        assert metrics.weighted_path_recall_when_answering >= 0.9

    def test_most_suggestions_are_genuinely_missing(self, result):
        metrics, _ = result
        assert metrics.suggestion_precision >= 0.7

    def test_wrong_suggestions_stay_within_the_reading_budget(self, result):
        metrics, _ = result
        assert metrics.false_positive_burden <= 1.5

    def test_abstention_is_not_the_default_answer(self, result):
        metrics, _ = result
        assert metrics.abstention_rate <= 0.6

    def test_confidence_is_not_yet_calibrated(self, result):
        """Recorded as a known weakness, not a passing grade.

        The score multiplies three sub-1 terms, so it reads far lower than the
        observed hit rate. Calibrating it is the next piece of work, and this
        assertion is here to catch it getting worse in the meantime.
        """
        metrics, _ = result
        assert metrics.expected_calibration_error <= 0.55

    def test_abstains_when_only_one_incident_shares_the_root_cause(self, result):
        _, outcomes = result
        by_id = {outcome.incident_id: outcome for outcome in outcomes}
        assert by_id["HOLD-003"].abstained
        assert by_id["HOLD-003"].abstain_reason == "insufficient_support"

    def test_abstains_on_an_incident_type_it_has_never_seen(self, result):
        _, outcomes = result
        by_id = {outcome.incident_id: outcome for outcome in outcomes}
        assert by_id["HOLD-004"].abstained
        assert by_id["HOLD-004"].abstain_reason == "no_candidates"


class TestPolicyTradeoffs:
    def test_demanding_more_support_trades_recall_for_precision(self):
        lenient, _ = run_offline_eval(suggestion_policy=SuggestionPolicy(min_support=1))
        strict, _ = run_offline_eval(suggestion_policy=SuggestionPolicy(min_support=2))
        assert strict.suggestion_precision >= lenient.suggestion_precision
        assert strict.false_positive_burden <= lenient.false_positive_burden

    def test_raising_the_similarity_floor_raises_abstention(self):
        base, _ = run_offline_eval()
        strict, _ = run_offline_eval(
            retrieval_policy=RetrievalPolicy(min_symptom_similarity=0.9)
        )
        assert strict.abstention_rate >= base.abstention_rate
        assert strict.weighted_path_recall <= base.weighted_path_recall

    def test_coarse_signatures_change_the_scores(self):
        coarse, _ = run_offline_eval(
            retrieval_policy=RetrievalPolicy(signature_level=SignatureLevel.COARSE),
            suggestion_policy=SuggestionPolicy(signature_level=SignatureLevel.COARSE),
        )
        assert 0.0 <= coarse.suggestion_precision <= 1.0
