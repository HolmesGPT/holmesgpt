from unittest.mock import Mock

import pytest

from holmes.decision.demo import DemoDecisionProvider, DemoReasoner, DemoTools
from holmes.decision.engine import DecisionAgent
from holmes.decision.models import (
    Budget, Candidate, Claim, Decision, DecisionState, DiagnosisDraft, ReasoningResult,
    TaskMemory, ToolOutput,
)
from holmes.decision.store import EvidenceStore


@pytest.fixture
def store():
    database = EvidenceStore.from_url("sqlite://")
    database.initialize()
    yield database
    database.engine.dispose()


def single_query_reasoner():
    reasoner = Mock()
    # Changing the candidate ID on every plan must not reset logical call history.
    reasoner.reason.side_effect = lambda state: ReasoningResult(memory=TaskMemory(), candidates=[Candidate(
        id=f"logs-{state.reasoning_calls}", tool="service_logs", arguments={"service": "checkout"},
        purpose="Read scoped service logs", direction="logs",
    )])
    reasoner.report.side_effect = lambda state: DiagnosisDraft(memory=DemoReasoner.memory(state))
    return reasoner


def call_replan_or_finish(state, actions):
    choice = next((key for key in actions if key.startswith("call:")),
                  "reason" if "reason" in actions else "finish")
    return Decision(choice=choice, confidence=1)


def run(store, reasoner=None, gateway=None, provider=None, budget=None, **kwargs):
    return DecisionAgent(
        reasoner or single_query_reasoner(), provider or DemoDecisionProvider(),
        gateway or DemoTools(), store, retry_wait=lambda _: 0, **kwargs,
    ).run(DecisionState(question="排查 checkout 超时", budget=budget or Budget()))


@pytest.mark.parametrize("field,value", [("summary", "DNS is definitely the root cause"), ("outcome", "confirmed")])
def test_model_cannot_supply_an_uncited_summary_or_confirmed_outcome(store, field, value):
    reasoner = single_query_reasoner()
    reasoner.report.side_effect = None
    reasoner.report.return_value = {"memory": {}, "causes": [], field: value}
    result = run(store, reasoner=reasoner)
    assert result.state.status == "failed"
    assert result.diagnosis.outcome == "invalid_report"
    assert value not in result.model_dump_json()
    assert store.load_diagnosis(result.state.task_id) == result.diagnosis
    assert len(result.state.evidence) == 1


def test_empty_causes_render_insufficient_evidence_even_after_a_successful_query(store):
    result = run(store)
    assert result.state.status == "completed"  # Execution ended; a root cause is not established.
    assert result.state.stop_reason == "decision_finish"
    assert result.diagnosis.outcome == "insufficient_evidence"
    assert result.diagnosis.summary == "当前证据不足，未形成诊断结论。"
    assert result.diagnosis.schema_version == 2


def test_report_refreshes_memory_and_commits_terminal_state_with_diagnosis(store):
    reasoner = single_query_reasoner()

    def report(state):
        saved = store.load(state.task_id)
        assert saved.status == "running"
        assert saved.pending_status == "completed"
        assert store.load_diagnosis(state.task_id) is None
        return DiagnosisDraft(
            memory=DemoReasoner.memory(state),
            causes=[Claim(text="Connection waiting is a candidate cause", evidence_ids=[state.evidence[0]["id"]])],
        )

    reasoner.report.side_effect = report
    result = run(store, reasoner=reasoner, budget=Budget(max_reasoning_calls=1))
    saved = store.load(result.state.task_id)
    assert saved.status == "completed"
    assert saved.pending_status is None
    assert len(saved.memory.facts) == 1
    assert result.diagnosis.outcome == "candidate_causes"
    assert "尚未确认根因" in result.diagnosis.summary
    reasoner.reason.assert_called_once()
    reasoner.report.assert_called_once()


def test_error_observation_cannot_be_promoted_to_a_fact_in_final_memory(store):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.return_value = ToolOutput(status="error", error="access denied", error_kind="permission")
    reasoner = single_query_reasoner()
    reasoner.report.side_effect = lambda state: DiagnosisDraft(memory=TaskMemory(facts=[
        Claim(text="The database is down", evidence_ids=[state.evidence[0]["id"]])
    ]))
    result = run(store, reasoner=reasoner, gateway=gateway)
    assert result.diagnosis.outcome == "invalid_report"
    assert result.state.memory.facts == []


def test_transient_retry_records_each_attempt_and_preserves_first_failure(store):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.side_effect = [TimeoutError("temporary timeout"), ToolOutput(status="success", data="recovered")]
    result = run(store, gateway=gateway)
    assert gateway.execute.call_count == result.state.tool_calls == 2
    attempts = [item for item in result.state.history if item.attempt_id]
    assert [item.status for item in attempts] == ["error", "success"]
    assert [item.attempt_no for item in attempts] == [1, 2]
    assert len({item.attempt_id for item in attempts}) == 2
    assert len({item.fingerprint for item in attempts}) == 1
    evidence = [store.get_evidence(result.state.task_id, item.evidence_id) for item in attempts]
    assert evidence[0].output.error_kind == "timeout"
    assert evidence[1].output.data == "recovered"
    assert [item.attempt_id for item in evidence] == [item.attempt_id for item in attempts]
    assert len(result.state.memory.facts) == 1  # Failed attempt is not a factual observation.


@pytest.mark.parametrize("output", [
    ToolOutput(status="error", error="HTTP 503 timeout; please retry"),
    ToolOutput(status="error", error="permission denied", error_kind="permission"),
    ToolOutput(status="error", error="invalid query", error_kind="invalid_arguments"),
    ToolOutput(status="approval_required", error="approval required"),
    ToolOutput(status="no_data", data=[]),
    ToolOutput(status="success", data="observed"),
])
def test_only_typed_transient_errors_are_retried(store, output):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.return_value = output
    run(store, gateway=gateway)
    gateway.execute.assert_called_once()


def test_retry_requires_operator_attestation_of_read_only_safety(store):
    gateway = Mock(wraps=DemoTools())
    gateway.catalog.return_value = [spec.model_copy(update={"retry_safe": False}) for spec in DemoTools().catalog()]
    gateway.execute.side_effect = TimeoutError("temporary timeout")
    run(store, gateway=gateway)
    gateway.execute.assert_called_once()


def test_retry_limit_cannot_be_reset_by_replanning_with_new_candidate_ids(store):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.side_effect = TimeoutError("source still unavailable")
    provider = Mock()
    provider.decide.side_effect = call_replan_or_finish
    result = run(store, gateway=gateway, provider=provider,
                 budget=Budget(max_reasoning_calls=30, max_no_progress=3))
    assert gateway.execute.call_count == 3
    assert result.state.reasoning_calls == 4  # Bootstrap, then three stalled replacement plans.
    assert result.state.no_progress == 3
    assert "reason" not in provider.decide.call_args.args[1]
    assert result.diagnosis.outcome == "insufficient_evidence"


@pytest.mark.parametrize("limit", [1, 2])
def test_every_retry_counts_against_the_global_tool_budget(store, limit):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.side_effect = TimeoutError("temporary timeout")
    provider = Mock(wraps=DemoDecisionProvider())
    result = run(store, gateway=gateway, provider=provider, budget=Budget(max_tool_calls=limit))
    assert gateway.execute.call_count == result.state.tool_calls == limit
    assert not any(key.startswith("call:") for key in provider.decide.call_args.args[1])
    assert "finish" in provider.decide.call_args.args[1]


def test_retry_after_outside_deadline_cannot_be_bypassed_by_a_new_plan(store):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.return_value = ToolOutput(status="error", error_kind="rate_limit", retry_after_seconds=10)
    provider = Mock()
    provider.decide.side_effect = call_replan_or_finish
    sleep = Mock()
    result = run(store, gateway=gateway, provider=provider, budget=Budget(max_seconds=5),
                 clock=lambda: 0, sleep=sleep)
    gateway.execute.assert_called_once()
    sleep.assert_not_called()
    assert result.state.history[1].retry_exhausted


def test_deadline_is_rechecked_after_retry_backoff(store):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.side_effect = TimeoutError("temporary timeout")
    now = [0.0]

    def sleep(_):
        now[0] = 20

    result = run(store, gateway=gateway, clock=lambda: now[0], sleep=sleep, budget=Budget(max_seconds=10))
    gateway.execute.assert_called_once()
    assert result.state.stop_reason == "time_budget"


@pytest.mark.parametrize("change", ["schema", "retry_policy"])
def test_retries_revalidate_tool_schema_and_policy_after_waiting(store, change):
    gateway = Mock(wraps=DemoTools())
    catalog = DemoTools().catalog()
    gateway.catalog.side_effect = lambda refresh=True: [spec.model_copy(deep=True) for spec in catalog]
    gateway.execute.side_effect = TimeoutError("temporary timeout")

    def sleep(_):
        if change == "schema":
            catalog[0].parameters["required"].append("new_argument")
        else:
            catalog[0].retry_safe = False

    result = run(store, gateway=gateway, sleep=sleep)
    gateway.execute.assert_called_once()
    assert any(event["kind"] == "tool_invalidated" for event in store.audit(result.state.task_id))


def test_database_failure_after_tool_result_is_not_a_retryable_tool_error(store):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.side_effect = TimeoutError("temporary timeout")
    checkpoint = store.checkpoint

    def fail_evidence(state, **kwargs):
        if kwargs["event"]["kind"] == "evidence":
            raise OSError("database unavailable")
        return checkpoint(state, **kwargs)

    store.checkpoint = fail_evidence
    with pytest.raises(OSError, match="database unavailable"):
        run(store, gateway=gateway)
    gateway.execute.assert_called_once()


def test_three_empty_sources_do_not_prevent_querying_the_fourth_source(store):
    gateway = Mock(wraps=DemoTools())
    gateway.execute.side_effect = [ToolOutput(status="no_data", data=[]) for _ in range(3)] + [
        ToolOutput(status="success", data="fourth source has relevant evidence")
    ]
    result = run(store, reasoner=DemoReasoner(), gateway=gateway)
    assert gateway.execute.call_count == 4
    assert [item["status"] for item in result.state.evidence] == ["no_data"] * 3 + ["success"]
    assert len(result.state.memory.facts) == 4
    assert result.state.status == "completed"


def test_last_candidate_can_finish_with_no_reasoning_budget_left(store):
    reasoner = single_query_reasoner()
    provider = Mock(wraps=DemoDecisionProvider())
    result = run(store, reasoner=reasoner, provider=provider, budget=Budget(max_reasoning_calls=1))
    assert result.state.status == "completed"
    assert result.state.reasoning_calls == 1
    assert result.state.tool_calls == 1
    assert set(provider.decide.call_args.args[1]) == {"finish", "need_input"}
    reasoner.reason.assert_called_once()


def test_decision_cannot_select_reason_after_its_budget_is_exhausted(store):
    reasoner = single_query_reasoner()
    provider = Mock()
    provider.decide.return_value = Decision(choice="reason", confidence=1)
    gateway = Mock(wraps=DemoTools())
    result = run(store, reasoner=reasoner, provider=provider, gateway=gateway, budget=Budget(max_reasoning_calls=1))
    assert result.state.status == "failed"
    assert result.state.stop_reason.startswith("decision_error:")
    reasoner.reason.assert_called_once()
    gateway.execute.assert_not_called()


def test_legacy_records_remain_readable_without_claiming_new_validation_or_replaying(store):
    legacy = DecisionState(question="old investigation").model_dump(mode="json")
    legacy.pop("pending_status")
    legacy["budget"].pop("max_tool_attempts")
    legacy["history"] = [{"choice": "call:logs", "status": "started", "fingerprint": "old-key"}]
    with store.engine.begin() as conn:
        conn.execute(store.tasks.insert().values(
            id=legacy["task_id"], revision=0, state=legacy,
            diagnosis={"summary": "Historical report", "causes": [], "verification_steps": [], "limitations": []},
        ))
    state = store.load(legacy["task_id"])
    diagnosis = store.load_diagnosis(state.task_id)
    assert diagnosis.summary == "Historical report"
    assert diagnosis.schema_version == 1
    assert diagnosis.outcome == "legacy_unverified"
    assert state.history[0].attempt_no == 0
    gateway = Mock(wraps=DemoTools())
    with pytest.raises(ValueError, match="not replayed"):
        DecisionAgent(DemoReasoner(), DemoDecisionProvider(), gateway, store).run(state)
    gateway.execute.assert_not_called()
