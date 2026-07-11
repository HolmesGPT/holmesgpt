import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from server import app

from holmes.core.tool_calling_llm import LLMResult


@pytest.fixture
def client():
    return TestClient(app)


@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_health_check_success(mock_create_toolcalling_llm, client):
    """Test successful health check execution that passes."""
    # Create mock AI with a mock LLM that has a model attribute
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"

    # The execute_check function calls ai.call() and expects an LLMResult
    # with a JSON string containing 'passed' and 'rationale'
    mock_response = LLMResult(
        result=json.dumps(
            {"passed": True, "rationale": "All systems are operational and healthy."}
        ),
        tool_calls=[],
    )
    mock_ai.call.return_value = mock_response
    mock_create_toolcalling_llm.return_value = mock_ai

    payload = {
        "query": "Are all pods running in the default namespace?",
        "timeout": 30,
        "mode": "monitor",
    }

    response = client.post(
        "/api/checks/execute", json=payload, headers={"X-Check-Name": "test-pod-check"}
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "pass"
    assert "passed" in data["message"].lower() or "pass" in data["message"].lower()
    assert data["rationale"] == "All systems are operational and healthy."
    assert data["model_used"] == "gpt-4"
    assert data["error"] is None
    assert data["duration"] >= 0


@patch("holmes.checks.checks_api.SlackDestination")
@patch("holmes.config.Config.create_toolcalling_llm")
def test_alert_passes_check_usage_to_slack(
    mock_create_toolcalling_llm, mock_slack_destination, client, monkeypatch
):
    monkeypatch.setenv("SLACK_TOKEN", "token")
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_response = LLMResult(
        result=json.dumps({"passed": False, "rationale": "Deployment is unavailable."}),
        tool_calls=[],
        total_cost=0.0425,
        total_tokens=15000,
        prompt_tokens=12000,
        completion_tokens=3000,
    )
    mock_ai.call.return_value = mock_response
    mock_create_toolcalling_llm.return_value = mock_ai

    response = client.post(
        "/api/checks/execute",
        json={
            "name": "unavailable-deployment",
            "query": "Is the deployment available?",
            "mode": "alert",
            "destinations": [{"type": "slack", "config": {"channel": "#alerts"}}],
        },
        headers={"X-Check-Name": "unavailable-deployment"},
    )

    assert response.status_code == 200
    llm_result = mock_slack_destination.return_value.send_issue.call_args.args[1]
    assert llm_result.total_cost == pytest.approx(0.0425)
    assert llm_result.total_tokens == 15000
    assert llm_result.prompt_tokens == 12000
    assert llm_result.completion_tokens == 3000
