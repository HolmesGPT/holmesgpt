import json
from datetime import datetime

import pytest
import requests
import responses

from holmes.checks.models import CheckStatus
from holmes.core.issue import Issue, IssueStatus
from holmes.core.tool_calling_llm import LLMResult
from holmes.plugins.destinations.webhook.plugin import (
    WEBHOOK_EVENT_TYPE,
    WEBHOOK_PAYLOAD_KEYS,
    WEBHOOK_SCHEMA_VERSION,
    WebhookDestination,
)

WEBHOOK_URL = "https://hooks.example.com/holmes/checks"


def _destination(method: str = "POST") -> WebhookDestination:
    return WebhookDestination(method=method, webhook_url=WEBHOOK_URL)


def _issue(**overrides) -> Issue:
    kwargs = dict(
        id="healthcheck-pod-health-1728000000",
        name="Health Check Failed: pod-health",
        source_type="HealthCheck",
        source_instance_id="test-cluster",
        presentation_status=IssueStatus.OPEN,
    )
    kwargs.update(overrides)
    return Issue(**kwargs)


def test_payload_schema_is_versioned_and_stable():
    payload = _destination()._create_event_payload(
        _issue(), LLMResult(result="finding")
    )

    assert tuple(payload.keys()) == WEBHOOK_PAYLOAD_KEYS
    assert payload["schema_version"] == WEBHOOK_SCHEMA_VERSION == 1
    assert payload["event_type"] == WEBHOOK_EVENT_TYPE == "holmes.check.result"
    # timestamp is ISO-8601 and parseable
    datetime.fromisoformat(payload["timestamp"])


def test_unknown_fields_are_null_rather_than_missing():
    payload = _destination()._create_event_payload(
        _issue(), LLMResult(result="finding")
    )

    for key in (
        "namespace",
        "rationale",
        "model_used",
        "duration_seconds",
        "status_url",
        "error",
    ):
        assert payload[key] is None, f"{key} should default to null"

    assert payload["check_name"] == "Health Check Failed: pod-health"
    assert payload["message"] == "finding"
    # No explicit status: inferred from presentation_status
    assert payload["result"] == "fail"


def test_check_context_populates_every_field():
    issue = _issue(
        url="https://holmes.example.com/checks/pod-health",
        raw={
            "check_name": "pod-health",
            "namespace": "prod",
            "status": "fail",
            "message": "Check failed. 2 pods are CrashLoopBackOff.",
            "rationale": "Found 2 crashlooping pods in namespace prod.",
            "model_used": "anthropic/claude-sonnet-4-5-20250929",
            "duration_seconds": 12.5,
            "error": None,
        },
    )

    payload = _destination()._create_event_payload(issue, LLMResult(result="ignored"))

    assert payload["issue_id"] == issue.id
    assert payload["check_name"] == "pod-health"
    assert payload["namespace"] == "prod"
    assert payload["result"] == "fail"
    assert payload["message"] == "Check failed. 2 pods are CrashLoopBackOff."
    assert payload["rationale"] == "Found 2 crashlooping pods in namespace prod."
    assert payload["model_used"] == "anthropic/claude-sonnet-4-5-20250929"
    assert payload["duration_seconds"] == 12.5
    assert payload["status_url"] == "https://holmes.example.com/checks/pod-health"
    assert payload["error"] is None


def test_legacy_check_runner_raw_keys_are_read():
    # holmes/checks/checks.py names the check "check" and the message "result"
    issue = _issue(
        raw={
            "check": "disk-usage",
            "result": "Check failed. root filesystem is 95% full.",
            "status": "fail",
        }
    )

    payload = _destination()._create_event_payload(issue, LLMResult())

    assert payload["check_name"] == "disk-usage"
    assert payload["message"] == "Check failed. root filesystem is 95% full."


def test_pass_status_is_forwarded_verbatim():
    issue = _issue(
        presentation_status=IssueStatus.CLOSED,
        raw={"check_name": "disk-usage", "status": "pass"},
    )

    payload = _destination()._create_event_payload(issue, LLMResult())

    assert payload["result"] == "pass"


def test_enum_status_is_serialised_to_its_value():
    issue = _issue(raw={"check_name": "disk-usage", "status": CheckStatus.ERROR})

    payload = _destination()._create_event_payload(issue, LLMResult())

    assert payload["result"] == "error"


def test_send_issue_posts_the_payload_as_json():
    issue = _issue(raw={"check_name": "pod-health", "status": "fail"})

    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, WEBHOOK_URL, json={"ok": True}, status=200)
        _destination().send_issue(issue, LLMResult(result="finding"))

        assert len(rsps.calls) == 1
        request = rsps.calls[0].request
        body = json.loads(request.body)

    assert body["schema_version"] == 1
    assert body["check_name"] == "pod-health"
    assert body["result"] == "fail"
    assert request.headers["Content-Type"] == "application/json"


def test_send_issue_honours_the_configured_method():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.PUT, WEBHOOK_URL, json={"ok": True}, status=200)
        _destination(method="PUT").send_issue(_issue(), LLMResult(result="finding"))

        assert rsps.calls[0].request.method == "PUT"


def test_send_issue_raise_http_errors_after_logging():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, WEBHOOK_URL, json={"error": "nope"}, status=500)
        # must not raise: notification failures are recorded by the caller
        with pytest.raises(requests.exceptions.RequestException):
            _destination().send_issue(_issue(), LLMResult(result="finding"))


def test_headers_default_to_json_and_are_not_shared_between_instances():
    first = _destination()
    second = _destination()

    assert first.headers["Content-Type"] == "application/json"
    assert second.headers["Content-Type"] == "application/json"
    assert first.headers is not second.headers
