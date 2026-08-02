import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from server import app

from holmes.checks.checks import _get_check_prompt, _parse_check_response, execute_check
from holmes.checks.models import Check, CheckStatus
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

    _, call_kwargs = mock_ai.call.call_args
    assert "response_format" not in call_kwargs


def test_parse_check_response_extracts_json_from_surrounding_text():
    response = LLMResult(
        result=(
            "I completed the requested investigation.\n"
            + json.dumps(
                {
                    "passed": False,
                    "rationale": "Alert found. Checked pods, events, metrics, and applied the requested patch.",
                }
            )
        ),
        tool_calls=[],
    )

    parsed = _parse_check_response(response)

    assert parsed.passed is False
    assert (
        parsed.rationale
        == "Alert found. Checked pods, events, metrics, and applied the requested patch."
    )


def test_execute_check_accepts_embedded_final_json_with_completed_rationale():
    mock_ai = MagicMock()
    mock_ai.call.return_value = LLMResult(
        result=(
            "Final result:\n"
            + json.dumps(
                {
                    "passed": True,
                    "rationale": "No active alerts remain after checking pods, events, metrics, and patch status.",
                }
            )
        ),
        tool_calls=[],
    )
    check = Check(
        name="multi-step-check",
        query=(
            "Find alerts, investigate pods, check events and metrics, "
            "patch if needed, then summarize."
        ),
    )

    result = execute_check(check, mock_ai)

    assert result.status == CheckStatus.PASS
    assert (
        result.rationale
        == "No active alerts remain after checking pods, events, metrics, and patch status."
    )
    _, call_kwargs = mock_ai.call.call_args
    assert "response_format" not in call_kwargs


def test_check_prompt_requires_completed_investigation_before_final_json():
    prompt = _get_check_prompt()

    assert "complete every listed step before deciding pass or fail" in prompt
    assert "must not say you are \"now investigating\"" in prompt
