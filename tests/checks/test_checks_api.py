import json
import pytest
import responses
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from server import app

from holmes.core.tool_calling_llm import LLMResult
from holmes.core.issue import Issue
from holmes.plugins.destinations.pagerduty.plugin import PagerDutyDestination


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


def test_pagerduty_payload_identifies_cluster():
    issue = Issue(
        id="healthcheck-kube-system-velero-health-1",
        name="Health Check Failed: kube-system-velero-health",
        source_instance_id="dev007",
        source_type="HealthCheck",
    )
    result = LLMResult(result="The check failed.", tool_calls=[])

    payload = PagerDutyDestination("routing-key")._create_event_payload(issue, result)

    assert payload["payload"]["summary"].startswith("Holmes Check Failed [dev007]")
    assert payload["payload"]["source"] == "dev007"
    assert payload["payload"]["custom_details"]["source_instance"] == "dev007"
    assert payload["dedup_key"] == "holmes-check-healthcheck-kube-system-velero-health-1"


def test_pagerduty_payload_keeps_the_check_identity_stable():
    first_issue = Issue(
        id="healthcheck-dev007-kube-system-velero-health-queryhash",
        name="Health Check Failed: kube-system-velero-health",
        source_instance_id="dev007",
        source_type="HealthCheck",
    )
    second_issue = first_issue.model_copy(
        update={"id": "healthcheck-dev007-kube-system-velero-health-queryhash"}
    )
    result = LLMResult(result="The check failed.", tool_calls=[])
    destination = PagerDutyDestination("routing-key")

    first_payload = destination._create_event_payload(first_issue, result)
    second_payload = destination._create_event_payload(second_issue, result)

    assert first_payload["dedup_key"] == second_payload["dedup_key"]
    assert first_payload["payload"]["summary"] == second_payload["payload"]["summary"]


def test_pagerduty_payload_can_resolve_an_incident():
    issue = Issue(
        id="healthcheck-kube-system-velero-health-1",
        name="Health Check Failed: kube-system-velero-health",
        source_instance_id="dev007",
        source_type="HealthCheck",
    )
    result = LLMResult(result="The check passed.", tool_calls=[])

    payload = PagerDutyDestination("routing-key")._create_event_payload(
        issue, result, event_action="resolve"
    )

    assert payload["event_action"] == "resolve"
    assert payload["dedup_key"] == "holmes-check-healthcheck-kube-system-velero-health-1"


def test_pagerduty_rejects_non_https_api_url():
    with pytest.raises(ValueError, match="must use HTTPS"):
        PagerDutyDestination("routing-key", "http://pagerduty.example/v2/enqueue")


def test_pagerduty_disables_redirects():
    issue = Issue(
        id="healthcheck-test-1",
        name="Health Check Failed: test",
        source_instance_id="dev007",
        source_type="HealthCheck",
    )
    result = LLMResult(result="The check failed.", tool_calls=[])

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "https://events.pagerduty.com/v2/enqueue",
            json={"status": "success"},
            status=302,
            headers={"Location": "https://redirect.example/v2/enqueue"},
        )

        assert not PagerDutyDestination("routing-key").send_issue(issue, result)
        assert len(rsps.calls) == 1


def test_pagerduty_accepts_any_https_api_url():
    destination = PagerDutyDestination("routing-key", "https://127.0.0.1/v2/enqueue")

    assert destination.api_url == "https://127.0.0.1/v2/enqueue"


def test_health_check_identity_ignores_operator_run_suffix():
    from holmes.checks.checks_api import _get_check_identity

    first = _get_check_identity(
        "kube-system-velero-health-20260911-090200-4cb5cb", "check query"
    )
    second = _get_check_identity(
        "kube-system-velero-health-20260911-090400-79f2fd", "check query"
    )

    assert first == second


@patch("holmes.checks.checks_api.PagerDutyDestination")
@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_health_check_pagerduty_uses_global_key(
    mock_create_toolcalling_llm, mock_pagerduty, client, monkeypatch
):
    """Send a failed alert through PagerDuty using the global integration key."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": False, "rationale": "A pod failed."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai
    monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", "global-key")

    response = client.post(
        "/api/checks/execute",
        json={
            "query": "Are the pods healthy?",
            "name": "pod-health",
            "mode": "alert",
            "destinations": [{"type": "pagerduty", "config": {}}],
        },
    )

    assert response.status_code == 200
    assert response.json()["notifications"] == [
        {"type": "pagerduty", "channel": None, "status": "sent", "error": None}
    ]
    mock_pagerduty.assert_called_once_with(
        integration_key="global-key",
        api_url="https://events.pagerduty.com/v2/enqueue",
    )
    mock_pagerduty.return_value.send_issue.assert_called_once()


@patch("holmes.checks.checks_api.PagerDutyDestination")
@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_health_check_pagerduty_resolves_on_pass(
    mock_create_toolcalling_llm, mock_pagerduty, client, monkeypatch
):
    """Resolve the stable PagerDuty incident when the check passes."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": True, "rationale": "All pods are healthy."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai
    monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", "global-key")

    response = client.post(
        "/api/checks/execute",
        json={
            "query": "Are the pods healthy?",
            "name": "pod-health-20260911-090400-79f2fd",
            "mode": "alert",
            "destinations": [{"type": "pagerduty", "config": {}}],
        },
    )

    assert response.status_code == 200
    assert response.json()["notifications"][0]["status"] == "sent"
    call = mock_pagerduty.return_value.send_issue.call_args
    assert call.kwargs["event_action"] == "resolve"
    assert call.args[0].name == "Health Check Failed: pod-health"


@patch("holmes.checks.checks_api.PagerDutyDestination")
@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_health_check_pagerduty_uses_global_key_for_approved_custom_url(
    mock_create_toolcalling_llm, mock_pagerduty, client, monkeypatch
):
    """Use the global key for an approved custom PagerDuty URL."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": False, "rationale": "A pod failed."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai
    monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", "global-key")

    response = client.post(
        "/api/checks/execute",
        json={
            "query": "Are the pods healthy?",
            "name": "pod-health",
            "mode": "alert",
            "destinations": [
                {
                    "type": "pagerduty",
                    "config": {"api_url": "https://events.eu.pagerduty.com/v2/enqueue"},
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["notifications"] == [
        {"type": "pagerduty", "channel": None, "status": "sent", "error": None}
    ]
    mock_pagerduty.assert_called_once_with(
        integration_key="global-key",
        api_url="https://events.eu.pagerduty.com/v2/enqueue",
    )


@patch("holmes.checks.checks_api.PagerDutyDestination")
@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_health_check_pagerduty_uses_inline_config(
    mock_create_toolcalling_llm, mock_pagerduty, client, monkeypatch
):
    """Per-check PagerDuty settings override the global environment."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": False, "rationale": "A pod failed."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai
    monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", "global-key")

    response = client.post(
        "/api/checks/execute",
        json={
            "query": "Are the pods healthy?",
            "name": "pod-health",
            "mode": "alert",
            "destinations": [
                {
                    "type": "pagerduty",
                    "config": {
                        "integration_key": "inline-key",
                        "api_url": "https://events.eu.pagerduty.com/v2/enqueue",
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    mock_pagerduty.assert_called_once_with(
        integration_key="inline-key",
        api_url="https://events.eu.pagerduty.com/v2/enqueue",
    )


@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_health_check_pagerduty_skips_without_key(
    mock_create_toolcalling_llm, client, monkeypatch
):
    """Report a skipped notification when no PagerDuty key is configured."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": False, "rationale": "A pod failed."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai
    monkeypatch.delenv("PAGERDUTY_INTEGRATION_KEY", raising=False)

    response = client.post(
        "/api/checks/execute",
        json={
            "query": "Are the pods healthy?",
            "name": "pod-health",
            "mode": "alert",
            "destinations": [{"type": "pagerduty", "config": {}}],
        },
    )

    assert response.status_code == 200
    assert response.json()["notifications"] == [
        {
            "type": "pagerduty",
            "channel": None,
            "status": "skipped",
            "error": "PAGERDUTY_INTEGRATION_KEY not configured",
        }
    ]


@patch("holmes.checks.checks_api.PagerDutyDestination")
@patch("holmes.config.Config.create_toolcalling_llm")
def test_execute_health_check_reports_pagerduty_failure(
    mock_create_toolcalling_llm, mock_pagerduty, client, monkeypatch
):
    """Report a failed notification when PagerDuty rejects the event."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": False, "rationale": "A pod failed."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai
    mock_pagerduty.return_value.send_issue.return_value = False
    monkeypatch.setenv("PAGERDUTY_INTEGRATION_KEY", "secret-key")

    response = client.post(
        "/api/checks/execute",
        json={
            "query": "Are the pods healthy?",
            "name": "pod-health",
            "mode": "alert",
            "destinations": [{"type": "pagerduty", "config": {}}],
        },
    )

    assert response.status_code == 200
    notification = response.json()["notifications"][0]
    assert notification["status"] == "failed"
    assert notification["error"] == "PagerDuty notification failed"
    assert "secret-key" not in response.text
