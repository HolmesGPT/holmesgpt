"""Model HTTP contracts + real MCP subprocess + reopened SQL database."""

import json

import responses

from tracepilot.config import LLMConfig, MCPServerConfig, ToolPolicy
from tracepilot.engine import DecisionAgent
from tracepilot.llm import HTTPCompletion
from tracepilot.mcp_gateway import MCPGateway
from tracepilot.models import DecisionState
from tracepilot.providers import JevDecisionProvider, StructuredReasoner
from tracepilot.scenarios import demo_scope
from tracepilot.store import EvidenceStore


def test_model_contracts_mcp_and_storage_together(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "test-key")
    scope = demo_scope()
    config = MCPServerConfig(command="{python}", args=["-m", "tracepilot.fixture_server"], tools={n: ToolPolicy(read_only=True) for n in ("service_traces", "service_logs")})
    gateway = MCPGateway({"obs": config}, scope)
    llm = HTTPCompletion(LLMConfig(provider="anthropic", model="fixture-model", api_key_env="TEST_LLM_KEY"))
    url = f"sqlite:///{tmp_path / 'evidence.db'}"
    store = EvidenceStore.from_url(url)
    store.initialize()

    def reasoning(request):
        body = json.loads(request.body)
        state = json.loads(body["messages"][0]["content"])
        evidence = state["evidence"]
        if "Produce a report draft" in body["system"]:
            draft = {"memory": {"facts": [{"text": "Observed a slow database span on checkout-a", "evidence_ids": [evidence[0]["id"]]}]},
                     "causes": [{"text": "Connection waiting is a candidate cause", "evidence_ids": [e["id"] for e in evidence]}]}
        else:
            args = scope.model_dump(mode="json")
            tool = "service_traces"
            if evidence:
                args["pod"] = json.loads(evidence[0]["preview"])["data"]["pod"]
                tool = "service_logs"
            draft = {"memory": {}, "candidates": [{"id": tool, "tool": "obs__" + tool, "arguments": args, "purpose": "Read incident evidence", "direction": "database"}]}
        return 200, {"Content-Type": "application/json"}, json.dumps({"content": [{"type": "text", "text": json.dumps(draft)}], "stop_reason": "end_turn"})

    def decision(request):
        body = json.loads(request.body)
        actions = body["questions"]["next_action"]["criteria"]
        chosen = next((k for k in actions if k.startswith("call:")), "reason" if len(body["state"]["evidence"]) < 2 else "finish")
        probabilities = {k: 0.98 if k == chosen else 0.02 / (len(actions) - 1) for k in actions}
        return 200, {"Content-Type": "application/json"}, json.dumps({"answers": {"next_action": {"type": "choice", "choice": chosen, "confidence": 0.98, "probabilities": probabilities}}})

    try:
        with responses.RequestsMock() as http:
            http.add_callback(responses.POST, "https://api.anthropic.com/v1/messages", callback=reasoning)
            http.add_callback(responses.POST, JevDecisionProvider.endpoint, callback=decision)
            result = DecisionAgent(StructuredReasoner(llm), JevDecisionProvider("test-key"), gateway, store).run(DecisionState(question="排查 checkout 超时", scope=scope))
        assert result.state.status == "completed", result.diagnosis
        assert result.state.tool_calls == 2 and result.state.reasoning_calls == 2
        assert result.state.evidence[1]["arguments"]["pod"] == "checkout-a"
        assert result.diagnosis.outcome == "candidate_causes"
        task_id = result.state.task_id
    finally:
        store.engine.dispose()
    reopened = EvidenceStore.from_url(url)
    try:
        assert reopened.verify(task_id)["valid"]
        assert len(reopened.load_diagnosis(task_id).causes[0].evidence_ids) == 2
        assert any(e["kind"] == "decision" for e in reopened.audit(task_id))
    finally:
        reopened.engine.dispose()
