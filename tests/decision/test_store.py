import os

import pytest

from holmes.decision.models import DecisionState, Evidence, ToolOutput
from holmes.decision.store import EvidenceStore, StaleCheckpoint


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path):
    if request.param == "postgresql":
        url = os.environ.get("HOLMES_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Set HOLMES_TEST_POSTGRES_URL for PostgreSQL integration tests")
    else:
        url = f"sqlite:///{tmp_path / 'evidence.db'}"
    result = EvidenceStore.from_url(url)
    result.initialize()
    yield result
    result.engine.dispose()


def record(state, data):
    return Evidence(task_id=state.task_id, tool="logs", source="test",
                    arguments={"service": "checkout", "minutes": 15},
                    output=ToolOutput(status="success", data=data))


def test_evidence_survives_reopen_and_is_not_lost_when_preview_is_truncated(store):
    state = DecisionState(question="timeout")
    store.create(state)
    evidence = record(state, "a" * 20000 + "关键证据")
    state.evidence.append(evidence.summary())
    store.checkpoint(state, event={"kind": "evidence"}, evidence=evidence)
    assert state.evidence[0]["truncated"]
    reopened = EvidenceStore.from_url(store.engine.url.render_as_string(hide_password=False))
    try:
        saved = reopened.get_evidence(state.task_id, evidence.id)
        assert saved.output.data.endswith("关键证据")
        assert reopened.load(state.task_id).evidence[0]["id"] == evidence.id
        page = reopened.read_evidence(state.task_id, evidence.id, offset=18000, limit=4000)
        assert "关键证据" in page["content"]
        assert page["next_offset"] is None
    finally:
        reopened.engine.dispose()


def test_stale_checkpoint_rolls_back_both_evidence_and_event(store):
    state = DecisionState(question="timeout")
    store.create(state)
    stale = state.model_copy(deep=True)
    store.checkpoint(state, event={"kind": "first"})
    rejected = record(stale, "must not be persisted")
    with pytest.raises(StaleCheckpoint):
        store.checkpoint(stale, event={"kind": "stale"}, evidence=rejected)
    with pytest.raises(KeyError):
        store.get_evidence(state.task_id, rejected.id)
    assert [event["kind"] for event in store.audit(state.task_id)] == ["first"]


def test_evidence_is_task_scoped_and_read_size_is_bounded(store):
    first = DecisionState(question="service A")
    second = DecisionState(question="service B")
    store.create(first)
    store.create(second)
    evidence = record(first, "private to A")
    store.checkpoint(first, event={"kind": "evidence"}, evidence=evidence)
    with pytest.raises(KeyError):
        store.read_evidence(second.task_id, evidence.id)
    with pytest.raises(ValueError):
        store.read_evidence(first.task_id, evidence.id, limit=12001)
    with pytest.raises(ValueError):
        store.checkpoint(second, event={"kind": "wrong_task"}, evidence=evidence)
