import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import responses

from holmes.decision.models import DecisionState, DiagnosisDraft, TaskMemory
from holmes.decision.policy import available_actions
from holmes.decision.providers import (
    DecisionServiceError, HolmesReasoner, JevDecisionProvider,
)


def payload(choice="finish", confidence=0.9):
    return {"model": "jev-1.13.0", "answers": {"next_action": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {"reason": 0.05, "finish": 0.9, "need_input": 0.05},
    }}, "usage": {"input_tokens": 200, "output_tokens": 30}}


def decide(provider):
    state = DecisionState(question="timeout")
    return provider.decide(state, available_actions(state))


def test_jev_uses_official_endpoint_and_choice_contract():
    with responses.RequestsMock() as http:
        http.add(responses.POST, JevDecisionProvider.endpoint, json=payload())
        decision = decide(JevDecisionProvider("test-only-key"))
        assert decision.choice == "finish"
        assert decision.confidence == 0.9
        request = http.calls[0].request
        body = json.loads(request.body)
        assert body["model"] == "jev-latest"
        assert body["state"]["question"] == "timeout"
        assert body["questions"]["next_action"]["type"] == "choice"
        assert request.headers["Authorization"] == "Bearer test-only-key"


@pytest.mark.parametrize("answer", [
    payload(choice="invented"), payload(confidence=2), payload(confidence=float("nan")),
    {"answers": {}},
    {"answers": {"next_action": {"type": "noul", "noul": 1}}},
])
def test_invalid_jev_responses_fail_closed(answer):
    with responses.RequestsMock() as http:
        http.add(responses.POST, JevDecisionProvider.endpoint, json=answer)
        with pytest.raises(DecisionServiceError):
            decide(JevDecisionProvider("test-key"))


def test_jev_transient_error_retries_but_auth_error_does_not():
    with responses.RequestsMock() as http:
        http.add(responses.POST, JevDecisionProvider.endpoint, status=529)
        http.add(responses.POST, JevDecisionProvider.endpoint, json=payload())
        decide(JevDecisionProvider("test-key"))
        assert len(http.calls) == 2
    with responses.RequestsMock() as http:
        http.add(responses.POST, JevDecisionProvider.endpoint, status=401)
        with pytest.raises(DecisionServiceError, match="401"):
            decide(JevDecisionProvider("test-key"))
        assert len(http.calls) == 1


def test_missing_key_does_not_enable_demo_fallback():
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        JevDecisionProvider("")


def test_holmes_llm_is_used_for_structured_reasoning_without_tool_execution():
    llm = Mock()
    llm.completion.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='{"memory":{"hypotheses":[{"text":"possible dependency latency"}]},"candidates":[]}'
    ))])
    result = HolmesReasoner(llm).reason(DecisionState(question="timeout"))
    assert result.memory.hypotheses[0].evidence_ids == []
    args = llm.completion.call_args.kwargs
    assert args["tools"] == []
    assert args["response_format"] == {"type": "json_object"}
    assert "timeout" in args["messages"][1]["content"]


def test_report_schema_is_validated():
    llm = Mock()
    llm.completion.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=DiagnosisDraft(memory=TaskMemory(), limitations=["No observations"]).model_dump_json()
    ))])
    result = HolmesReasoner(llm).report(DecisionState(question="timeout"))
    assert result.causes == []


def test_jev_receives_exact_runtime_actions_when_reasoning_is_unavailable():
    state = DecisionState(question="timeout", reasoning_calls=5)
    actions = available_actions(state)
    answer = payload()
    answer["answers"]["next_action"]["probabilities"] = {"finish": 0.9, "need_input": 0.1}
    with responses.RequestsMock() as http:
        http.add(responses.POST, JevDecisionProvider.endpoint, json=answer)
        decision = JevDecisionProvider("test-key").decide(state, actions)
        body = json.loads(http.calls[0].request.body)
        assert body["questions"]["next_action"]["criteria"] == actions
        assert "reason" not in actions
        assert decision.choice == "finish"
