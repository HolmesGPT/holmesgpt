import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from server import app

from holmes.core.tool_calling_llm import LLMResult, RelayRefusal


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


def _refusal(status_code: int, message: str) -> RelayRefusal:
    """A refusal as ToolCallingLLM re-raises it: the platform's own message
    and the status it refused with (ROB-1389)."""
    return RelayRefusal(message, status_code)


def _execute(client):
    return client.post(
        "/api/checks/execute",
        json={"query": "Are all pods running?", "timeout": 30, "mode": "monitor"},
        headers={"X-Check-Name": "test-pod-check"},
    )


@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_check_maps_a_disabled_account_to_403(
    mock_create_toolcalling_llm, client
):
    """Setting up the LLM is refused when the account disabled Robusta-hosted
    models: the caller gets 403 and the platform's own remedy, not a 500."""
    mock_create_toolcalling_llm.side_effect = _refusal(403, DISABLED_MESSAGE)

    response = _execute(client)

    assert response.status_code == 403
    assert response.json()["detail"] == DISABLED_MESSAGE


@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_check_maps_a_stale_token_to_401(mock_create_toolcalling_llm, client):
    message = "Your session has expired. Reconnect the cluster to the platform."
    mock_create_toolcalling_llm.side_effect = _refusal(401, message)

    response = _execute(client)

    assert response.status_code == 401
    assert response.json()["detail"] == message


@patch("holmes.config.Config.create_toolcalling_llm")
def test_a_refusal_during_the_call_is_reported_as_a_check_error(
    mock_create_toolcalling_llm, client
):
    """execute_check turns any failure of the LLM call into an ERROR result
    rather than an HTTP status, so what matters there is that the message the
    user reads is the platform's, not litellm's rendering of it."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "Robusta/gpt-5"
    mock_ai.call.side_effect = _refusal(403, DISABLED_MESSAGE)
    mock_create_toolcalling_llm.return_value = mock_ai

    response = _execute(client)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert DISABLED_MESSAGE in data["error"]
