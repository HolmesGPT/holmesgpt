import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from holmes.core.tool_calling_llm import LLMResult, RelayRefusal
from server import app


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


DISABLED_MESSAGE = (
    "Robusta-hosted models are disabled for this account. Configure a model on "
    "the cluster, or enable Robusta-hosted models in Settings > LLM Models."
)


def _execute(client):
    return client.post(
        "/api/checks/execute",
        json={"query": "Are all pods running?", "timeout": 30, "mode": "monitor"},
        headers={"X-Check-Name": "test-pod-check"},
    )


@patch("holmes.config.Config.create_toolcalling_llm")
def test_a_relay_refusal_is_reported_as_a_check_error(
    mock_create_toolcalling_llm, client
):
    """A check's LLM call runs inside execute_check, which turns any failure
    into an ERROR result rather than an HTTP status. The platform's refusal is
    one such failure; what reaches the caller is its own sentence (ROB-1389),
    not litellm's rendering of it."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "Robusta/gpt-5"
    mock_ai.call.side_effect = RelayRefusal(DISABLED_MESSAGE, 403)
    mock_create_toolcalling_llm.return_value = mock_ai

    response = _execute(client)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert DISABLED_MESSAGE in data["error"]
