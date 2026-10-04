import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
import responses
from typer.testing import CliRunner

from tracepilot.cli import app
from tracepilot.config import LLMConfig
from tracepilot.llm import HTTPCompletion, ModelServiceError
from tracepilot.models import Budget, DecisionState
from tracepilot.observability_server import ObservabilityClient, build_server
from tracepilot.providers import JevDecisionProvider, StructuredReasoner
from tracepilot.scenarios import demo_scope


@pytest.mark.parametrize("provider", ["anthropic", "openai-compatible"])
def test_http_reasoning_adapter(provider, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-key")
    config = LLMConfig(provider=provider, model="test-model", api_key_env="TEST_MODEL_KEY")
    content = '{"memory":{"hypotheses":[{"text":"dependency latency"}]},"candidates":[]}'
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        payload = {"content": [{"type": "text", "text": content}], "stop_reason": "end_turn"}
    else:
        url = "https://api.openai.com/v1/chat/completions"
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    with responses.RequestsMock() as http:
        http.post(url, json=payload)
        result = StructuredReasoner(HTTPCompletion(config)).reason(DecisionState(question="timeout"))
        assert result.memory.hypotheses[0].text == "dependency latency"
        body = json.loads(http.calls[0].request.body)
        assert body["model"] == "test-model"
        assert "tools" not in body
        assert "Never invent" in str(body)


def test_auth_failure_not_retried_and_no_secret_in_error(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "private-test-value")
    config = LLMConfig(provider="anthropic", model="test", api_key_env="TEST_MODEL_KEY")
    with responses.RequestsMock() as http:
        http.post("https://api.anthropic.com/v1/messages", status=401)
        with pytest.raises(ModelServiceError) as failure:
            StructuredReasoner(HTTPCompletion(config)).reason(DecisionState(question="timeout"))
        assert len(http.calls) == 1
        assert "private-test-value" not in str(failure.value)


def test_truncated_response_rejected(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-key")
    config = LLMConfig(provider="anthropic", model="test", api_key_env="TEST_MODEL_KEY")
    with responses.RequestsMock() as http:
        http.post("https://api.anthropic.com/v1/messages", json={"stop_reason": "max_tokens", "content": []})
        with pytest.raises(ModelServiceError, match="output budget"):
            StructuredReasoner(HTTPCompletion(config)).reason(DecisionState(question="timeout"))


def test_context_budget_prevents_network():
    state = DecisionState(question="timeout", context={"oversized": "x" * 10000}, budget=Budget(max_context_chars=8000))
    client = Mock()
    with pytest.raises(ValueError, match="max_context_chars"):
        StructuredReasoner(client).reason(state)
    client.completion.assert_not_called()
    provider = JevDecisionProvider("test-key")
    provider._post = Mock()
    with pytest.raises(ValueError, match="max_context_chars"):
        provider.decide(state, {"finish": "Finish", "need_input": "Need input"})
    provider._post.assert_not_called()


def test_only_configured_sources_advertised():
    server = build_server({"LOKI_URL": "https://telemetry.example.test"})
    assert {t.name for t in anyio.run(server.list_tools)} == {"service_logs"}


@pytest.mark.parametrize("backend,tool,path,extra,response", [
    ("LOKI", "service_logs", "/loki/api/v1/query_range", {"contains": 'error" | drop namespace'}, {"status": "success", "data": {"result": []}}),
    ("PROMETHEUS", "service_metrics", "/api/v1/query_range", {"metric": "db_pool_pending"}, {"status": "success", "data": {"result": []}}),
    ("TEMPO", "service_traces", "/api/search", {}, {"traces": []}),
    ("KUBERNETES", "service_instances", "/api/v1/namespaces/demo/pods", {}, {"items": [{"metadata": {"name": "checkout-a"}, "spec": {"secret": "should-not-return"}, "status": {"phase": "Running"}}]}),
])
def test_backend_http_query_contracts(backend, tool, path, extra, response):
    base = "https://telemetry.example.test"
    server = build_server({f"{backend}_URL": base, f"{backend}_TOKEN": "test-token"})
    with responses.RequestsMock() as http:
        http.get(base + path, json=response)
        result = anyio.run(server.call_tool, tool, {**demo_scope().model_dump(mode="json"), **extra})
        request = http.calls[0].request
        params = parse_qs(urlsplit(request.url).query)
        assert request.headers["Authorization"] == "Bearer test-token"
        if backend == "KUBERNETES":
            assert params["labelSelector"] == ["app.kubernetes.io/name=checkout"]
            assert "current_snapshot" in str(result) and "should-not-return" not in str(result)
        else:
            query = params.get("query", params.get("q"))[0]
            assert "checkout" in query and "demo" in query
            assert "start" in params and "end" in params
        if backend == "LOKI":
            assert '\\" | drop namespace' in params["query"][0]


def test_metric_expression_cannot_escape_scope():
    server = build_server({"PROMETHEUS_URL": "https://telemetry.example.test"})
    with responses.RequestsMock() as http:
        with pytest.raises(Exception):
            anyio.run(server.call_tool, "service_metrics", {**demo_scope().model_dump(mode="json"), "metric": "up{} or secret_metric"})
        assert len(http.calls) == 0


def test_backend_error_is_not_healthy_data():
    server = build_server({"LOKI_URL": "https://telemetry.example.test"})
    with responses.RequestsMock() as http:
        http.get("https://telemetry.example.test/loki/api/v1/query_range", status=403)
        with pytest.raises(Exception):
            anyio.run(server.call_tool, "service_logs", demo_scope().model_dump(mode="json"))


def test_duplicate_label_mapping_rejected():
    with pytest.raises(ValueError, match="distinct"):
        ObservabilityClient({"OBS_SERVICE_LABEL": "namespace"}).labels("checkout", "prod")


def test_cli_reopen_paginate_and_verify(tmp_path):
    runner = CliRunner()
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    result = runner.invoke(app, ["demo", "--scenario", "slow-dependency", "--database-url", url, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    task_id = data["state"]["task_id"]
    assert data["state"]["tool_calls"] == 4
    inspection = runner.invoke(app, ["inspect", task_id, "--database-url", url])
    assert inspection.exit_code == 0, inspection.output
    assert json.loads(inspection.output)["integrity"]["valid"]
    page = runner.invoke(app, ["evidence", task_id, data["state"]["evidence"][0]["id"], "--database-url", url, "--limit", "30"])
    assert len(json.loads(page.output)["content"]) == 30
    checked = runner.invoke(app, ["verify", task_id, "--database-url", url])
    assert checked.exit_code == 0
    assert json.loads(checked.output)["checked"] == 4


def test_help_brand():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "TracePilot" in result.output and "Holmes" not in result.output
