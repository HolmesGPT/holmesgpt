import json
from unittest.mock import MagicMock, patch

import pytest
import responses
from fastapi.testclient import TestClient
from holmes.core.issue import Issue
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.destinations.webhook.plugin import WebhookDestination
from server import app

WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"

RATIONALE = "Payment pods are crash-looping."


@pytest.fixture
def client():
    return TestClient(app)


def _mock_failing_check(mock_create_toolcalling_llm):
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": False, "rationale": RATIONALE}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai


def _execute(client, destinations):
    return client.post(
        "/api/checks/execute",
        json={
            "query": "Is the payment service healthy?",
            "name": "default/payment-check",
            "timeout": 30,
            "mode": "alert",
            "destinations": destinations,
        },
    )
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, WEBHOOK_URL, json={"ok": True}, status=200)
        response = _execute(
            client,
            [{"type": "webhook", "config": {"url": WEBHOOK_URL, "timeout": 5}}],
        )

        request = rsps.calls[0].request
        assert request.method == "POST"
        body = json.loads(request.body)
        assert body["version"] == 1
        assert body["name"] == "payment-check"
        assert body["namespace"] == "default"
        assert body["status"] == "fail"
        assert body["message"] == f"Check failed. {RATIONALE}"
        assert body["query"] == "Is the payment service healthy?"
        assert body["rationale"] == RATIONALE
        assert body["model_used"] == "gpt-4"
        assert isinstance(body["duration"], (int, float))
        assert body["timestamp"]

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "fail"
    notification = data["notifications"][0]
    assert notification["type"] == "webhook"
    assert notification["status"] == "sent"


@patch("holmes.config.Config.create_toolcalling_llm")
def test_webhook_destination_renders_env_templates_in_headers(
    mock_create_toolcalling_llm, client, monkeypatch
):
    """Header values support {{ env.VAR }} templating for secrets."""
    monkeypatch.setenv("WEBHOOK_TOKEN", "s3cr3t-token")
    _mock_failing_check(mock_create_toolcalling_llm)

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, WEBHOOK_URL, json={"ok": True}, status=200)
        response = _execute(
            client,
            [
                {
                    "type": "webhook",
                    "config": {
                        "url": WEBHOOK_URL,
                        "headers": {
                            "Authorization": "Bearer {{ env.WEBHOOK_TOKEN }}",
                            "X-Static": "holmes",
                        },
                    },
                }
            ],
        )
        headers = rsps.calls[0].request.headers
        assert headers["Authorization"] == "Bearer s3cr3t-token"
        assert headers["X-Static"] == "holmes"
        assert headers["Content-Type"] == "application/json"

    assert response.status_code == 200
    assert response.json()["notifications"][0]["status"] == "sent"


@patch("holmes.config.Config.create_toolcalling_llm")
def test_webhook_destination_honors_custom_method(mock_create_toolcalling_llm, client):
    """The optional method field overrides the default POST."""
    _mock_failing_check(mock_create_toolcalling_llm)

    with responses.RequestsMock() as rsps:
        rsps.add(responses.PUT, WEBHOOK_URL, json={"ok": True}, status=200)
        response = _execute(
            client,
            [{"type": "webhook", "config": {"url": WEBHOOK_URL, "method": "put"}}],
        )
        assert rsps.calls[0].request.method == "PUT"

    assert response.status_code == 200
    assert response.json()["notifications"][0]["status"] == "sent"


@patch("holmes.config.Config.create_toolcalling_llm")
def test_webhook_destination_non_2xx_marks_notification_failed(
    mock_create_toolcalling_llm, client
):
    """A non-2xx response surfaces as a failed notification, not a request error."""
    _mock_failing_check(mock_create_toolcalling_llm)

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, WEBHOOK_URL, body="boom", status=500)
        response = _execute(
            client, [{"type": "webhook", "config": {"url": WEBHOOK_URL}}]
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "fail"
    notification = data["notifications"][0]
    assert notification["type"] == "webhook"
    assert notification["status"] == "failed"
    assert "500" in notification["error"]


@patch("holmes.config.Config.create_toolcalling_llm")
def test_webhook_destination_connection_error_marks_notification_failed(
    mock_create_toolcalling_llm, client
):
    """Connection errors surface as a failed notification."""
    _mock_failing_check(mock_create_toolcalling_llm)

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            WEBHOOK_URL,
            body=ConnectionError("name resolution failed"),
        )
        response = _execute(
            client, [{"type": "webhook", "config": {"url": WEBHOOK_URL}}]
        )

    assert response.status_code == 200
    notification = response.json()["notifications"][0]
    assert notification["status"] == "failed"
    assert notification["error"]


@patch("holmes.config.Config.create_toolcalling_llm")
def test_webhook_destination_missing_url_is_skipped(
    mock_create_toolcalling_llm, client
):
    """A webhook destination without a url is skipped, mirroring slack's
    missing-credentials behavior."""
    _mock_failing_check(mock_create_toolcalling_llm)

    response = _execute(client, [{"type": "webhook", "config": {}}])

    assert response.status_code == 200
    notification = response.json()["notifications"][0]
    assert notification["type"] == "webhook"
    assert notification["status"] == "skipped"
    assert "url" in notification["error"]


@patch("holmes.config.Config.create_toolcalling_llm")
def test_webhook_destination_not_fired_for_passing_check(
    mock_create_toolcalling_llm, client
):
    """Passing checks skip destinations, same as slack/pagerduty."""
    mock_ai = MagicMock()
    mock_ai.llm.model = "gpt-4"
    mock_ai.call.return_value = LLMResult(
        result=json.dumps({"passed": True, "rationale": "All good."}),
        tool_calls=[],
    )
    mock_create_toolcalling_llm.return_value = mock_ai

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.POST, WEBHOOK_URL, json={"ok": True}, status=200)
        response = _execute(
            client, [{"type": "webhook", "config": {"url": WEBHOOK_URL}}]
        )
        assert len(rsps.calls) == 0

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pass"
    assert not data["notifications"]


def test_webhook_payload_without_namespace():
    """Check names without a '<namespace>/' prefix yield a null namespace."""
    issue = Issue(
        id="i1",
        name="Health Check Failed: standalone-check",
        source_instance_id="cluster",
        source_type="HealthCheck",
        raw={"check_name": "standalone-check", "status": "fail"},
    )
    dest = WebhookDestination(url=WEBHOOK_URL)
    payload = dest._create_payload(issue, LLMResult(result="r", tool_calls=[]))
    assert payload["name"] == "standalone-check"
    assert payload["namespace"] is None


def test_webhook_destination_requires_url():
    with pytest.raises(ValueError, match="url"):
        WebhookDestination(url="")
