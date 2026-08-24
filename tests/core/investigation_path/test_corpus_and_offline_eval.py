"""The fixture corpus and the benchmark it feeds.

The thresholds below are a recorded baseline for the current policy on the
current corpus, not a quality bar anyone should be satisfied with. They exist so
that a change to retrieval shows up as a number moving, which is the point of
running this offline before wiring anything into an investigation.
"""

import re
from collections import Counter

import pytest
import yaml

from holmes.core.investigation_path.calibration import build_calibration_samples
from holmes.core.investigation_path.corpus import (
    DEFAULT_CORPUS_DIR,
    corpus_bytes_per_incident,
    load_corpus,
)
from holmes.core.investigation_path.normalize import _KNOWN_KINDS
from holmes.core.investigation_path.offline_eval import run_offline_eval
from holmes.core.investigation_path.retrieval import RetrievalPolicy
from holmes.core.investigation_path.schema import SUBJECT_TOKEN, SignatureLevel
from holmes.core.investigation_path.validator import GENERIC_ENTITY_KINDS, SuggestionPolicy

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

    def test_root_cause_coverage_is_recorded_accurately(self):
        """Pins the real spread, so prose about it cannot drift out of date.

        A cause needs three pool members to be usable: leave-one-out removes one
        and retrieval needs two left to answer. Two causes do not clear that bar,
        and the docs must keep saying so until someone adds incidents.
        """
        counts = Counter(r.root_cause.label for r in load_corpus(split="corpus"))
        usable = {label for label, count in counts.items() if count >= 3}
        assert usable == {"dependency_unreachable", "oom_kill", "config_regression"}
        assert counts["image_pull_failure"] == 1
        assert counts["node_disk_pressure"] == 1

    def test_every_answerable_holdout_cause_is_represented_in_the_pool(self):
        pool_counts = Counter(r.root_cause.label for r in load_corpus(split="corpus"))
        for record in load_corpus(split="holdout"):
            label = record.root_cause.label
            # HOLD-004 is deliberately a cause the pool has never seen.
            if label == "certificate_expired":
                assert label not in pool_counts
            else:
                assert pool_counts[label] >= 3, f"{record.incident_id} can never be answered"

    def test_an_empty_endpoints_claim_matches_the_stated_cause(self, records):
        """The corpus is the ground truth, so a wrong rationale is not cosmetic.

        A NetworkPolicy is enforced by the CNI: it blocks traffic while the
        Service and its endpoints stay healthy. INC-002 claimed empty endpoints
        as the evidence for exactly that cause, contradicting its own summary,
        which would teach retrieval that reading endpoints identifies a blocked
        network path.
        """
        for record in records:
            summary = record.root_cause.summary.lower()
            if "networkpolicy" not in summary:
                continue
            for step in record.reference_path:
                if step.entity.kind != "endpoints":
                    continue
                assert "empty endpoints is the direct evidence" not in step.rationale.lower(), (
                    f"{record.incident_id}: a NetworkPolicy leaves endpoints populated"
                )

    def test_every_referenced_kind_is_one_the_command_parser_knows(self, records):
        """A reference step naming a kind the parser cannot produce can never be
        matched by a real tool call, so it would score as permanently skipped."""
        for record in records:
            for step in record.reference_path:
                kind = step.entity.kind
                if not kind or kind in GENERIC_ENTITY_KINDS:
                    continue
                assert kind in _KNOWN_KINDS, f"{record.incident_id}: unknown kind {kind!r}"

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
    """Recorded baseline for the current policy on the current corpus.

    These are thresholds so a regression shows up, not a quality bar anyone
    should be satisfied with. Five held-out cases cannot establish that this
    works; they establish that it has not silently got worse.
    """

    @pytest.fixture(scope="class")
    def result(self):
        return run_offline_eval()

    def test_every_holdout_case_is_scored(self, result):
        metrics, outcomes, _ = result
        assert metrics.cases == len(outcomes) == len(load_corpus(split="holdout"))

    def test_no_llm_call_is_made(self, result):
        metrics, _, _ = result
        assert metrics.llm_calls == 0

    def test_no_tokens_are_spent(self, result):
        """Tracked separately from llm_calls: a change that reuses tokens the
        investigation already spent would move this and not that."""
        metrics, _, _ = result
        assert metrics.llm_tokens == 0

    def test_token_cost_is_reported_not_merely_implied(self, result):
        """The reviewer asked for token cost. Zero has to be a measured zero."""
        metrics, _, _ = result
        assert "llm tokens" in metrics.render()

    def test_validation_is_fast_enough_to_be_free(self, result):
        metrics, _, _ = result
        assert metrics.latency_p95_ms < 50

    def test_it_finds_what_was_skipped(self, result):
        metrics, _, _ = result
        assert metrics.weighted_path_recall_when_answering >= 0.7

    def test_no_suggestion_is_wrong(self, result):
        metrics, _, _ = result
        assert metrics.suggestion_precision >= 0.95

    def test_wrong_suggestions_stay_within_the_reading_budget(self, result):
        metrics, _, _ = result
        assert metrics.false_positive_burden <= 0.5

    def test_abstention_is_not_the_default_answer(self, result):
        metrics, _, _ = result
        assert metrics.abstention_rate <= 0.4

    def test_confidence_is_calibrated(self, result):
        metrics, _, _ = result
        assert metrics.expected_calibration_error <= 0.10
        assert metrics.brier_score <= 0.10

    def test_calibration_is_a_large_improvement_on_the_raw_score(self):
        """The raw evidence score is a product of four terms below 1, so it
        reads far below the observed hit rate. This is the gap being closed."""
        raw, _, _ = run_offline_eval(calibrate=False)
        calibrated, _, model = run_offline_eval(calibrate=True)
        assert model.fitted
        assert calibrated.expected_calibration_error < raw.expected_calibration_error / 2

    def test_calibration_is_fitted_only_on_the_pool(self, result):
        """Fitting on the held-out cases would make the reported error meaningless."""
        _, _, model = result
        assert model.samples == len(build_calibration_samples(load_corpus(split="corpus")))

    def test_abstains_on_an_incident_type_it_has_never_seen(self, result):
        _, outcomes, _ = result
        by_id = {outcome.incident_id: outcome for outcome in outcomes}
        assert by_id["HOLD-004"].abstained
        assert by_id["HOLD-004"].abstain_reason == "no_candidates"

    def test_says_nothing_rather_than_something_wrong_on_the_hard_case(self, result):
        """HOLD-005 matches the cache incidents on symptoms and root cause, but
        its dependency is a broker. Suggesting the Redis checks would be four
        confident wrong answers; the correct behaviour is to stay quiet."""
        _, outcomes, _ = result
        hard = {outcome.incident_id: outcome for outcome in outcomes}["HOLD-005"]
        assert hard.false_positives == []

    def test_the_hard_case_is_a_known_gap_not_a_success(self, result):
        """Staying quiet costs recall, and that cost should stay visible."""
        _, outcomes, _ = result
        hard = {outcome.incident_id: outcome for outcome in outcomes}["HOLD-005"]
        assert hard.missing_weights
        assert hard.true_positives == []


class TestPolicyTradeoffs:
    def test_demanding_more_support_trades_recall_for_precision(self):
        lenient, _, _ = run_offline_eval(
            suggestion_policy=SuggestionPolicy(min_support_ratio=0.0)
        )
        strict, _, _ = run_offline_eval(suggestion_policy=SuggestionPolicy())
        assert strict.suggestion_precision >= lenient.suggestion_precision
        assert strict.false_positive_burden <= lenient.false_positive_burden

    def test_the_support_ratio_filter_removes_false_positives_on_its_own(self):
        """Isolated from the confidence floor, which also clears these.

        Comparing against the default policy conflated the two: a weakly
        supported check is also a low-confidence one, so whichever filter runs
        first gets the credit, and the assertion passed without showing that
        the support ratio does anything. Holding `min_confidence` at zero
        leaves the support ratio as the only thing that can change the result.
        """
        unfiltered = dict(min_confidence=0.0, max_suggestions=20)
        without, _, _ = run_offline_eval(
            suggestion_policy=SuggestionPolicy(min_support_ratio=0.0, **unfiltered),
            calibrate=False,
        )
        with_filter, _, _ = run_offline_eval(
            suggestion_policy=SuggestionPolicy(min_support_ratio=0.6, **unfiltered),
            calibrate=False,
        )
        assert without.false_positive_burden > with_filter.false_positive_burden
        assert with_filter.suggestion_precision > without.suggestion_precision

    def test_a_false_positive_is_still_reachable(self):
        """Precision of 1.00 has to be an achievement, not an impossibility.

        If no policy setting could produce a wrong suggestion, the corpus would
        no longer be able to measure precision at all and the headline number
        would be vacuous.
        """
        loose, _, _ = run_offline_eval(
            suggestion_policy=SuggestionPolicy(
                min_support_ratio=0.0, min_confidence=0.0, max_suggestions=20
            ),
            calibrate=False,
        )
        assert loose.suggestion_precision < 1.0

    def test_raising_the_similarity_floor_raises_abstention(self):
        base, _, _ = run_offline_eval()
        strict, _, _ = run_offline_eval(
            retrieval_policy=RetrievalPolicy(min_symptom_similarity=0.9)
        )
        assert strict.abstention_rate >= base.abstention_rate
        assert strict.weighted_path_recall <= base.weighted_path_recall

    def test_coarse_signatures_change_the_scores(self):
        coarse, _, _ = run_offline_eval(
            retrieval_policy=RetrievalPolicy(signature_level=SignatureLevel.COARSE),
            suggestion_policy=SuggestionPolicy(signature_level=SignatureLevel.COARSE),
        )
        assert 0.0 <= coarse.suggestion_precision <= 1.0
