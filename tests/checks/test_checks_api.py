import json
import pytest
import responses
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from server import app

from holmes.core.tool_calling_llm import LLMResult, RelayRefusal
from holmes.plugins.destinations.webhook.plugin import WEBHOOK_PAYLOAD_KEYS


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


@patch("holmes.config.Config.create_toolcalling_llm")
def test_failed_check_posts_versioned_webhook_payload(
    mock_create_toolcalling_llm, client
):
    """The webhook body carries the full check context (name, namespace, result,
    message, rationale, model, duration) under a stable, versioned schema."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps(
            {"passed": False, "rationale": "pod checkout-1 is CrashLoopBackOff"}
        ),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai

    webhook_url = "https://hooks.example.com/holmes/checks"
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, webhook_url, json={"ok": True}, status=200)

        response = client.post(
            "/api/checks/execute",
            json={
                "name": "pod-health",
                "query": "Are all pods in the shop namespace healthy?",
                "mode": "alert",
                "namespace": "shop",
                "destinations": [
                    {"type": "webhook", "config": {"webhook_url": webhook_url}}
                ],
            },
        )

        assert response.status_code == 200
        assert len(rsps.calls) == 1
        body = json.loads(rsps.calls[0].request.body)

    assert tuple(body.keys()) == WEBHOOK_PAYLOAD_KEYS
    assert body["schema_version"] == 1
    assert body["check_name"] == "pod-health"
    assert body["namespace"] == "shop"
    assert body["result"] == "fail"
    assert "CrashLoopBackOff" in body["message"]
    assert body["rationale"] == "pod checkout-1 is CrashLoopBackOff"
    assert body["model_used"] == "gpt-4"
    assert body["duration_seconds"] >= 0
    assert body["status_url"] is None
    assert body["error"] is None
