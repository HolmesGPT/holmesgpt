from unittest.mock import Mock

import pytest

from holmes.decision.demo import DemoDecisionProvider, DemoReasoner, DemoTools
from holmes.decision.engine import DecisionAgent, READ_EVIDENCE
from holmes.decision.models import (
    Budget, Candidate, Claim, Decision, DecisionState, Diagnosis,
    ReasoningResult, TaskMemory, ToolOutput,
)
from holmes.decision.store import EvidenceStore


@pytest.fixture
def store():
    result = EvidenceStore.from_url("sqlite://")
    result.initialize()
    yield result
    result.engine.dispose()


def test_full_investigation_persists_memory_raw_evidence_and_direction_changes(store):
    state = DecisionState(question="checkout timeout")
    result = DecisionAgent(DemoReasoner(), DemoDecisionProvider(), DemoTools(), store).run(state)
    assert result.state.status == "completed"
    assert result.state.tool_calls == 4
    assert result.state.reasoning_calls == 2  # Four tool calls do not require four LLM rounds.
    saved = store.load(state.task_id)
    assert len(saved.memory.facts) == 4
    evidence_id = saved.evidence[0]["id"]
    assert "SQLTransient" in str(store.get_evidence(state.task_id, evidence_id).output.data)
    assert store.load_diagnosis(state.task_id) == result.diagnosis
    started = [e for e in store.audit(state.task_id) if e["kind"] == "tool_started"]
    assert any(e["previous_direction"] != e["direction"] and e["previous_direction"] for e in started)


def test_low_confidence_never_executes_and_stops_at_reasoning_budget(store):
    tools = Mock(wraps=DemoTools())
    decisions = Mock()
    decisions.decide.return_value = Decision(choice="call:service_logs", confidence=0.2)
    result = DecisionAgent(DemoReasoner(), decisions, tools, store).run(
        DecisionState(question="timeout", budget=Budget(max_reasoning_calls=2))
    )
    assert result.state.stop_reason == "reasoning_budget"
    tools.execute.assert_not_called()
    assert sum(e["kind"] == "low_confidence" for e in store.audit(result.state.task_id)) == 2


@pytest.mark.parametrize("candidate", [
    Candidate(id="bad", tool="unknown", arguments={}, purpose="inspect", direction="logs"),
    Candidate(id="bad", tool="service_logs", arguments={"service": 42}, purpose="inspect", direction="logs"),
    Candidate(id="bad", tool="service_logs", arguments={}, purpose="inspect", direction="logs"),
])
def test_invalid_actions_are_rejected_before_decision_and_execution(store, candidate):
    reasoner = Mock(wraps=DemoReasoner())
    reasoner.reason.return_value = ReasoningResult(memory=TaskMemory(), candidates=[candidate])
    provider = Mock(wraps=DemoDecisionProvider())
    tools = Mock(wraps=DemoTools())
    result = DecisionAgent(reasoner, provider, tools, store).run(
        DecisionState(question="timeout", budget=Budget(max_reasoning_calls=1))
    )
    tools.execute.assert_not_called()
    provider.decide.assert_not_called()
    assert result.state.history[0].feedback


def test_memory_with_invented_citations_is_rejected(store):
    reasoner = Mock(wraps=DemoReasoner())
    reasoner.reason.return_value = ReasoningResult(
        memory=TaskMemory(facts=[Claim(text="database failed", evidence_ids=["invented"])]),
    )
    tools = Mock(wraps=DemoTools())
    result = DecisionAgent(reasoner, DemoDecisionProvider(), tools, store).run(
        DecisionState(question="timeout", budget=Budget(max_reasoning_calls=1))
    )
    assert result.state.memory.facts == []
    tools.execute.assert_not_called()


def test_schema_changed_while_jev_decides_does_not_execute_old_arguments(store):
    tools = DemoTools()
    original = tools.catalog()
    changed = [s.model_copy(deep=True) for s in original]
    changed[0].parameters["properties"]["service"] = {"const": "other-service"}
    gateway = Mock(wraps=tools)
    gateway.catalog.side_effect = [original, changed, changed]
    result = DecisionAgent(DemoReasoner(), DemoDecisionProvider(), gateway, store).run(
        DecisionState(question="timeout", budget=Budget(max_steps=1))
    )
    gateway.execute.assert_not_called()
    assert any(e["kind"] == "tool_invalidated" for e in store.audit(result.state.task_id))


def test_repeated_proposals_execute_once(store):
    candidate = Candidate(id="logs", tool="service_logs", arguments={"service": "checkout"},
                          purpose="inspect", direction="logs")
    reasoner = Mock(wraps=DemoReasoner())
    reasoner.reason.return_value = ReasoningResult(memory=TaskMemory(), candidates=[candidate])
    tools = Mock(wraps=DemoTools())
    result = DecisionAgent(reasoner, DemoDecisionProvider(), tools, store).run(DecisionState(question="timeout"))
    assert result.state.tool_calls == 1
    assert tools.execute.call_count == 1


def test_repeated_identical_outputs_trigger_no_progress_stop(store):
    tools = Mock(wraps=DemoTools())
    tools.execute.return_value = ToolOutput(status="success", data="unchanged")
    result = DecisionAgent(DemoReasoner(), DemoDecisionProvider(), tools, store).run(
        DecisionState(question="timeout", budget=Budget(max_no_progress=2))
    )
    assert result.state.stop_reason == "no_progress"
    assert result.state.tool_calls == 3


def test_tool_exception_becomes_durable_error_and_never_confirms_a_cause(store):
    tools = Mock(wraps=DemoTools())
    tools.execute.side_effect = TimeoutError("source query timed out")
    result = DecisionAgent(DemoReasoner(), DemoDecisionProvider(), tools, store).run(
        DecisionState(question="timeout", budget=Budget(max_no_progress=1))
    )
    assert result.state.stop_reason == "no_progress"
    record = store.get_evidence(result.state.task_id, result.state.evidence[0]["id"])
    assert record.output.status == "error"
    assert "timed out" in record.output.error
    assert result.diagnosis.causes == []


def test_approval_required_stops_without_auto_approving(store):
    tools = Mock(wraps=DemoTools())
    tools.execute.return_value = ToolOutput(status="approval_required", error="Operator permission required")
    result = DecisionAgent(DemoReasoner(), DemoDecisionProvider(), tools, store).run(DecisionState(question="timeout"))
    assert result.state.status == "needs_input"
    assert result.state.stop_reason == "tool_requires_approval"
    assert tools.execute.call_count == 1


def test_decision_service_failure_is_visible_and_not_silently_replaced(store):
    provider = Mock()
    provider.decide.side_effect = RuntimeError("unavailable")
    tools = Mock(wraps=DemoTools())
    result = DecisionAgent(DemoReasoner(), provider, tools, store).run(DecisionState(question="timeout"))
    assert result.state.status == "failed"
    assert result.state.stop_reason.startswith("decision_error")
    tools.execute.assert_not_called()


def test_report_with_hallucinated_evidence_is_rejected(store):
    reasoner = Mock(wraps=DemoReasoner())
    reasoner.report.return_value = Diagnosis(summary="definitely fixed", causes=[
        Claim(text="root cause", evidence_ids=["invented"])
    ])
    result = DecisionAgent(reasoner, DemoDecisionProvider(), DemoTools(), store).run(DecisionState(question="timeout"))
    assert result.state.status == "failed"
    assert result.state.stop_reason == "report_validation_failed"
    assert result.diagnosis.causes == []


def test_time_budget_is_rechecked_after_slow_decision(store):
    now = [0.0]
    provider = Mock()

    def decide(state):
        now[0] = 100
        return Decision(choice="call:service_logs", confidence=1)

    provider.decide.side_effect = decide
    tools = Mock(wraps=DemoTools())
    result = DecisionAgent(DemoReasoner(), provider, tools, store, clock=lambda: now[0]).run(
        DecisionState(question="timeout", budget=Budget(max_seconds=10))
    )
    assert result.state.stop_reason == "time_budget"
    tools.execute.assert_not_called()


def test_tool_call_budget_is_enforced(store):
    result = DecisionAgent(DemoReasoner(), DemoDecisionProvider(), DemoTools(), store).run(
        DecisionState(question="timeout", budget=Budget(max_tool_calls=2))
    )
    assert result.state.stop_reason == "tool_budget"
    assert result.state.tool_calls == 2


def test_persistence_failure_prevents_unaudited_tool_execution(store):
    tools = Mock(wraps=DemoTools())
    original = store.checkpoint

    def checkpoint(state, **kwargs):
        if kwargs["event"]["kind"] == "tool_started":
            raise OSError("database unavailable")
        return original(state, **kwargs)

    store.checkpoint = checkpoint
    with pytest.raises(OSError):
        DecisionAgent(DemoReasoner(), DemoDecisionProvider(), tools, store).run(DecisionState(question="timeout"))
    tools.execute.assert_not_called()


def test_evidence_reader_page_reaches_model_without_second_truncation(store):
    tools = Mock(wraps=DemoTools())
    tools.execute.return_value = ToolOutput(status="success", data="a" * 5000 + "tail-marker")
    reasoner = Mock(wraps=DemoReasoner())

    def reason(state):
        if not state.evidence:
            return ReasoningResult(memory=TaskMemory(), candidates=[Candidate(
                id="logs", tool="service_logs", arguments={"service": "checkout"},
                purpose="inspect", direction="logs",
            )])
        if len(state.evidence) == 1:
            return ReasoningResult(memory=TaskMemory(), candidates=[Candidate(
                id="read", tool=READ_EVIDENCE.name,
                arguments={"evidence_id": state.evidence[0]["id"], "offset": 0, "limit": 6000},
                purpose="inspect truncated result", direction="logs",
            )])
        return ReasoningResult(memory=TaskMemory())

    reasoner.reason.side_effect = reason
    reasoner.report.side_effect = lambda state: Diagnosis(
        summary="Attempt to cite the reader instead of the observation",
        causes=[Claim(text="cause", evidence_ids=[state.evidence[1]["id"]])],
    )
    result = DecisionAgent(reasoner, DemoDecisionProvider(), tools, store).run(DecisionState(question="timeout"))
    assert result.state.evidence[0]["truncated"]
    page = result.state.evidence[1]
    assert not page["truncated"]
    assert "tail-marker" in page["preview"]

    # Successful retrieval cannot become independent support for a cause.
    # Reports must cite the original observation and its execution status.
    assert result.state.stop_reason == "report_validation_failed"
    assert result.diagnosis.causes == []
