import subprocess
import sys
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from tracepilot.config import ScopeBinding
from tracepilot.engine import DecisionAgent
from tracepilot.models import DecisionState, InvestigationScope
from tracepilot.scenarios import ScenarioDecisionProvider, ScenarioReasoner, ScenarioTools, demo_scope
from tracepilot.store import EvidenceStore


@pytest.fixture
def store(tmp_path):
    value = EvidenceStore.from_url(f"sqlite:///{tmp_path / 'evidence.db'}")
    value.initialize()
    yield value
    value.engine.dispose()


def run(store, scenario="db-pool", gateway=None, reasoner=None):
    return DecisionAgent(reasoner or ScenarioReasoner(), ScenarioDecisionProvider(), gateway or ScenarioTools(scenario), store).run(
        DecisionState(question="排查 checkout 超时", scope=demo_scope()))


@pytest.mark.parametrize("scenario,status,outcome,calls", [
    ("db-pool", "completed", "candidate_causes", 5), ("slow-dependency", "completed", "candidate_causes", 4),
    ("no-data", "completed", "insufficient_evidence", 3), ("denied", "needs_input", "insufficient_evidence", 2),
])
def test_evidence_changes_path_and_report(store, scenario, status, outcome, calls):
    result = run(store, scenario)
    assert result.state.status == status
    assert result.diagnosis.outcome == outcome
    assert result.state.tool_calls == calls
    if scenario in {"db-pool", "slow-dependency"}:
        assert result.state.reasoning_calls > 1
        assert next(e for e in result.state.evidence if e["tool"] == "service_logs")["arguments"]["pod"] == "checkout-a"
        assert ("service_config" in {e["tool"] for e in result.state.evidence}) == (scenario == "db-pool")
    assert store.verify(result.state.task_id) == {"valid": True, "checked": calls, "full_record_checks": calls, "errors": []}


@pytest.mark.parametrize("field,value", [("service", "billing"), ("namespace", "private"),
                                        ("start", "2026-09-28T07:59:59Z"), ("end", "2026-09-28T08:15:01Z"),
                                        ("start", "2026-09-28T08:00:00"), ("end", 42)])
def test_scope_rejects_widened_or_invalid_arguments(field, value):
    args = demo_scope().model_dump(mode="json")
    args[field] = value
    with pytest.raises(PermissionError):
        ScopeBinding().validate_arguments(args, demo_scope())


def test_scope_accepts_narrowing_and_equivalent_timezone():
    args = demo_scope().model_dump(mode="json")
    args.update(start="2026-09-28T16:01:00+08:00", end="2026-09-28T08:05:00Z")
    ScopeBinding().validate_arguments(args, demo_scope())
    with pytest.raises(ValueError):
        InvestigationScope(service="a", namespace="b", start="2026-09-28", end="2026-09-29")


@pytest.mark.parametrize("field,value", [("source", "other-source"), ("arguments", {"service": "other"}),
                                        ("output", {"status": "success", "data": "forged"})])
def test_verify_detects_payload_and_provenance_corruption(store, field, value):
    result = run(store)
    eid = result.state.evidence[0]["id"]
    record = store.get_evidence(result.state.task_id, eid).model_dump(mode="json")
    record[field] = value
    with store.engine.begin() as conn:
        conn.execute(update(store.evidence).where(store.evidence.c.id == eid).values(record=record))
    checked = store.verify(result.state.task_id)
    assert not checked["valid"]
    assert any(e["error"] == "record_digest_mismatch" for e in checked["errors"])


def test_corrupt_evidence_prevents_report_generation(store):
    gateway = ScenarioTools("db-pool")
    original = gateway.execute
    reasoner = Mock(wraps=ScenarioReasoner())

    def corrupt(candidate):
        with store.engine.begin() as conn:
            conn.execute(update(store.evidence).values(record={"broken": True}))
        return original(candidate)

    gateway.execute = corrupt
    result = run(store, gateway=gateway, reasoner=reasoner)
    reasoner.report.assert_not_called()
    assert result.state.status == "failed"
    assert result.diagnosis.outcome == "invalid_report"


def test_output_schema_blocks_bad_data(store):
    gateway = ScenarioTools("db-pool")
    original = gateway.catalog
    gateway.catalog = lambda refresh=True: [s.model_copy(update={"output_schema": {"type": "object", "required": ["nonexistent"]}}) for s in original()]
    result = run(store, gateway=gateway)
    assert all(e["status"] == "error" for e in result.state.evidence)
    assert not result.diagnosis.causes


def test_approval_before_gateway_io(store):
    gateway = Mock(wraps=ScenarioTools("db-pool"))
    gateway.catalog.return_value = [s.model_copy(update={"approval_required": True}) for s in ScenarioTools("db-pool").catalog()]
    result = run(store, gateway=gateway)
    gateway.execute.assert_not_called()
    assert result.state.status == "needs_input"


def test_no_upstream_imports():
    result = subprocess.run([sys.executable, "-c", "import sys; import tracepilot.cli; assert not any(n == 'holmes' or n.startswith('holmes.') for n in sys.modules)"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
