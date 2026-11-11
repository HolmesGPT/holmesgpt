"""Tests for the FireHydrant investigations toolset and its server-side poller."""

import json
from unittest.mock import MagicMock, Mock

import pytest
import responses
from pydantic import ValidationError

from holmes.core.models import ChatResponse
from holmes.core.tools import StructuredToolResultStatus, ToolsetStatusEnum
from holmes.plugins.toolsets.firehydrant.firehydrant import (
    FIREHYDRANT_TOOLSET_NAME,
    FireHydrantApiError,
    FireHydrantClient,
    FireHydrantConfig,
    FireHydrantToolset,
    compact_incident,
)
from holmes.plugins.toolsets.firehydrant.poller import (
    FireHydrantInvestigationsPoller,
    build_investigation_prompt,
    describe_investigation,
    summarize_analysis,
)

API_URL = "https://api.staging.firehydrant.io"
API_KEY = "fhb-test-key"

INVESTIGATION = {
    "id": "inv-123",
    "status": "pending",
    "requested_via": "command_center",
    "incident": {
        "id": "inc-456",
        "number": 42,
        "name": "Checkout latency",
        "severity": "SEV1",
        "incident_url": "https://app.firehydrant.io/incidents/inc-456",
    },
}

INCIDENT = {
    "id": "inc-456",
    "number": 42,
    "name": "Checkout latency",
    "severity": "SEV1",
    "priority": "P1",
    "current_milestone": "investigating",
    "started_at": "2026-11-06T10:00:00Z",
    "description": "p99 latency on checkout-api above 5s",
    "incident_url": "https://app.firehydrant.io/incidents/inc-456",
    "services": [{"id": "svc-1", "name": "checkout-api"}, {"id": "svc-2", "name": "payments-db"}],
    "environments": [{"name": "production"}],
    "role_assignments": [{"user": {"name": "someone"}}],
    "conference_bridges": [],
    "labels": {},
    "tag_list": ["checkout"],
    "last_note": {"body": "Rolled back checkout-api to v1.2.3, no change"},
    "lifecycle_phases": [
        {
            "name": "Active",
            "milestones": [
                {"slug": "started", "occurred_at": "2026-11-06T10:00:00Z"},
                {"slug": "acknowledged", "occurred_at": None},
            ],
        }
    ],
}


class TestFireHydrantConfig:
    def test_defaults(self):
        config = FireHydrantConfig(api_key=API_KEY)
        assert config.api_url == "https://api.firehydrant.io"
        assert config.poll_investigations is True
        assert config.poll_interval_seconds == 30
        assert config.request_timeout_seconds == 30
        assert config.model is None

    def test_api_key_is_required(self):
        with pytest.raises(ValidationError):
            FireHydrantConfig()
        assert FireHydrantConfig.has_required_fields()

    def test_poll_interval_lower_bound(self):
        with pytest.raises(ValidationError):
            FireHydrantConfig(api_key=API_KEY, poll_interval_seconds=1)


class TestFireHydrantClient:
    def test_list_investigations_sends_auth_and_filters(self):
        client = FireHydrantClient(API_URL, API_KEY)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                f"{API_URL}/v1/investigations",
                json={"data": [INVESTIGATION], "pagination": {"count": 1}},
                match=[
                    responses.matchers.query_param_matcher({"status": "pending", "per_page": "5"}),
                    responses.matchers.header_matcher({"Authorization": f"Bearer {API_KEY}"}),
                ],
            )
            data = client.list_investigations(status="pending", per_page=5)
        assert data["data"][0]["id"] == "inv-123"

    def test_claim_posts_external_id(self):
        client = FireHydrantClient(API_URL, API_KEY)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/v1/investigations/inv-123/claim",
                json={**INVESTIGATION, "status": "in_progress", "external_id": "holmes-run-1"},
                status=201,
                match=[responses.matchers.json_params_matcher({"external_id": "holmes-run-1"})],
            )
            data = client.claim_investigation("inv-123", external_id="holmes-run-1")
        assert data["status"] == "in_progress"

    def test_claim_conflict_raises_with_status(self):
        client = FireHydrantClient(API_URL, API_KEY)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/v1/investigations/inv-123/claim",
                json={"error": "already claimed"},
                status=409,
            )
            with pytest.raises(FireHydrantApiError) as exc_info:
                client.claim_investigation("inv-123", external_id="x")
        assert exc_info.value.status_code == 409
        assert "already claimed" in str(exc_info.value)
        assert "/v1/investigations/inv-123/claim" in str(exc_info.value)

    def test_complete_investigation_body(self):
        client = FireHydrantClient(API_URL, API_KEY)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/v1/investigations/inv-123/complete",
                json={**INVESTIGATION, "status": "completed"},
                status=201,
                match=[
                    responses.matchers.json_params_matcher(
                        {
                            "status": "completed",
                            "summary": "Pool exhausted",
                            "result": {"markdown": "# Report\n\nPool exhausted"},
                        }
                    )
                ],
            )
            client.complete_investigation(
                "inv-123", status="completed", summary="Pool exhausted", markdown="# Report\n\nPool exhausted"
            )

    def test_complete_failed_investigation_body(self):
        client = FireHydrantClient(API_URL, API_KEY)
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                f"{API_URL}/v1/investigations/inv-123/complete",
                json={**INVESTIGATION, "status": "failed"},
                status=201,
                match=[
                    responses.matchers.json_params_matcher(
                        {"status": "failed", "summary": "Holmes failed", "error": "boom"}
                    )
                ],
            )
            client.complete_investigation("inv-123", status="failed", summary="Holmes failed", error="boom")


class TestCompactIncident:
    def test_keeps_key_fields_and_drops_noise(self):
        compact = compact_incident(INCIDENT)
        assert compact["number"] == 42
        assert compact["services"] == ["checkout-api", "payments-db"]
        assert compact["environments"] == ["production"]
        assert compact["last_note"] == "Rolled back checkout-api to v1.2.3, no change"
        assert compact["milestones"] == [{"milestone": "started", "occurred_at": "2026-11-06T10:00:00Z"}]
        assert "role_assignments" not in compact
        assert "conference_bridges" not in compact
        assert "labels" not in compact  # empty values are dropped


class TestFireHydrantToolset:
    def _toolset(self, **config):
        toolset = FireHydrantToolset()
        toolset.enabled = True
        toolset.config = {"api_url": API_URL, "api_key": API_KEY, **config}
        return toolset

    def test_tools(self):
        toolset = FireHydrantToolset()
        assert toolset.name == FIREHYDRANT_TOOLSET_NAME
        assert {t.name for t in toolset.tools} == {
            "firehydrant_list_investigations",
            "firehydrant_get_investigation",
            "firehydrant_get_incident",
        }
        assert toolset.missing_config  # api_key is required, so it is never auto-enabled

    def test_prerequisites_pass_on_healthy_api(self):
        toolset = self._toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{API_URL}/v1/investigations", json={"data": []})
            toolset.check_prerequisites(silent=True)
        assert toolset.status == ToolsetStatusEnum.ENABLED
        assert isinstance(toolset.config, FireHydrantConfig)

    def test_prerequisites_fail_on_bad_key(self):
        toolset = self._toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{API_URL}/v1/investigations", json={"error": "unauthorized"}, status=401)
            toolset.check_prerequisites(silent=True)
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert "401" in (toolset.error or "")
        assert "unauthorized" in (toolset.error or "")

    def test_prerequisites_fail_on_invalid_config(self):
        toolset = FireHydrantToolset()
        toolset.enabled = True
        toolset.config = {"api_url": API_URL}
        toolset.check_prerequisites(silent=True)
        assert toolset.status == ToolsetStatusEnum.FAILED
        assert "api_key" in (toolset.error or "")

    def test_get_incident_tool_returns_compact_incident(self):
        toolset = self._toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{API_URL}/v1/investigations", json={"data": []})
            toolset.check_prerequisites(silent=True)
            rsps.add(responses.GET, f"{API_URL}/v1/incidents/inc-456", json=INCIDENT)
            tool = next(t for t in toolset.tools if t.name == "firehydrant_get_incident")
            result = tool._invoke({"incident_id": "inc-456"}, MagicMock())
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data["services"] == ["checkout-api", "payments-db"]

    def test_list_tool_reports_api_errors(self):
        toolset = self._toolset()
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{API_URL}/v1/investigations", json={"data": []})
            toolset.check_prerequisites(silent=True)
            rsps.add(responses.GET, f"{API_URL}/v1/investigations", json={"error": "nope"}, status=403)
            tool = next(t for t in toolset.tools if t.name == "firehydrant_list_investigations")
            result = tool._invoke({"status": "pending"}, MagicMock())
        assert result.status == StructuredToolResultStatus.ERROR
        assert result.return_code == 403
        assert "nope" in (result.error or "")


class TestPrompt:
    def test_prompt_includes_incident_context(self):
        prompt = build_investigation_prompt(INVESTIGATION, compact_incident(INCIDENT))
        assert "incident #42: Checkout latency" in prompt
        assert "Severity: SEV1" in prompt
        assert "Impacted services: checkout-api, payments-db" in prompt
        assert "p99 latency on checkout-api above 5s" in prompt
        assert "Rolled back checkout-api" in prompt
        assert '"id": "inv-123"' in prompt

    def test_summarize_analysis_skips_headings_and_markdown(self):
        analysis = "## Root cause\n\n**Connection pool** exhaustion in `payments-db`.\n\n## Findings\n- ..."
        assert summarize_analysis(analysis) == "Connection pool exhaustion in payments-db."

    def test_summarize_analysis_truncates(self):
        summary = summarize_analysis("word " * 200)
        assert len(summary) <= 500
        assert summary.endswith("…")

    def test_describe_investigation(self):
        assert describe_investigation(INVESTIGATION) == "investigation inv-123 (incident #42 'Checkout latency')"


@pytest.fixture
def ready_toolset():
    toolset = FireHydrantToolset()
    toolset.enabled = True
    toolset.config = FireHydrantConfig(api_url=API_URL, api_key=API_KEY, poll_interval_seconds=5)
    toolset.status = ToolsetStatusEnum.ENABLED
    return toolset


@pytest.fixture
def config_with_toolset(ready_toolset):
    config = MagicMock()
    executor = MagicMock()
    executor.toolsets = [ready_toolset]
    config.cached_tool_executor = executor
    return config


@pytest.fixture
def chat_function():
    def chat(request, _http_request):
        return ChatResponse(
            analysis="**Connection pool** exhaustion in payments-db.\n\n## Findings\n\n- evidence",
            conversation_history=[],
            tool_calls=[],
        )

    return Mock(side_effect=chat)


@pytest.fixture
def poller(config_with_toolset, chat_function):
    return FireHydrantInvestigationsPoller(config=config_with_toolset, dal=None, chat_function=chat_function)


def _request_body(rsps, url_suffix):
    """The JSON body of the one request made to the URL ending with url_suffix."""
    bodies = [json.loads(call.request.body) for call in rsps.calls if call.request.url.endswith(url_suffix)]
    assert len(bodies) == 1, f"expected one call to {url_suffix}, got {len(bodies)}"
    return bodies[0]


def _add_pending(rsps, investigations):
    rsps.add(
        responses.GET,
        f"{API_URL}/v1/investigations",
        json={"data": investigations},
        match=[responses.matchers.query_param_matcher({"status": "pending", "per_page": "20"})],
    )


class TestPoller:
    def test_claims_investigates_and_completes(self, poller, ready_toolset, chat_function):
        with responses.RequestsMock() as rsps:
            _add_pending(rsps, [INVESTIGATION])
            rsps.add(
                responses.POST,
                f"{API_URL}/v1/investigations/inv-123/claim",
                json={**INVESTIGATION, "status": "in_progress"},
                status=201,
            )
            rsps.add(responses.GET, f"{API_URL}/v1/incidents/inc-456", json=INCIDENT)
            rsps.add(responses.GET, f"{API_URL}/v1/investigations/inv-123", json={**INVESTIGATION, "status": "in_progress"})
            rsps.add(
                responses.POST,
                f"{API_URL}/v1/investigations/inv-123/complete",
                json={**INVESTIGATION, "status": "completed"},
                status=201,
            )
            assert poller.poll_once(ready_toolset) == 1
            claim_body = _request_body(rsps, "/claim")
            complete_body = _request_body(rsps, "/complete")

        assert claim_body["external_id"].startswith(f"holmes-{poller.holmes_id}-")

        chat_request = chat_function.call_args[0][0]
        assert "incident #42: Checkout latency" in chat_request.ask
        assert "p99 latency on checkout-api above 5s" in chat_request.ask
        assert chat_request.stream is False
        assert chat_request.request_type == "firehydrant_investigation"
        assert chat_request.source_ref == "inv-123"

        assert complete_body["status"] == "completed"
        assert complete_body["summary"] == "Connection pool exhaustion in payments-db."
        assert complete_body["result"]["markdown"].startswith("**Connection pool** exhaustion")

    def test_uses_configured_model_and_system_prompt(self, poller, ready_toolset, chat_function):
        ready_toolset.config = FireHydrantConfig(
            api_url=API_URL, api_key=API_KEY, model="gpt-4.1", additional_system_prompt="Be terse."
        )
        with responses.RequestsMock() as rsps:
            _add_pending(rsps, [INVESTIGATION])
            rsps.add(responses.POST, f"{API_URL}/v1/investigations/inv-123/claim", json={}, status=201)
            rsps.add(responses.GET, f"{API_URL}/v1/incidents/inc-456", json=INCIDENT)
            rsps.add(responses.GET, f"{API_URL}/v1/investigations/inv-123", json={"status": "in_progress"})
            rsps.add(responses.POST, f"{API_URL}/v1/investigations/inv-123/complete", json={}, status=201)
            poller.poll_once(ready_toolset)
        chat_request = chat_function.call_args[0][0]
        assert chat_request.model == "gpt-4.1"
        assert chat_request.additional_system_prompt == "Be terse."

    def test_skips_investigation_claimed_by_another_engine(self, poller, ready_toolset, chat_function):
        with responses.RequestsMock() as rsps:
            _add_pending(rsps, [INVESTIGATION])
            rsps.add(
                responses.POST,
                f"{API_URL}/v1/investigations/inv-123/claim",
                json={"error": "already claimed"},
                status=409,
            )
            assert poller.poll_once(ready_toolset) == 0
        chat_function.assert_not_called()

    def test_canceled_investigation_is_not_completed(self, poller, ready_toolset, chat_function):
        with responses.RequestsMock() as rsps:
            _add_pending(rsps, [INVESTIGATION])
            rsps.add(responses.POST, f"{API_URL}/v1/investigations/inv-123/claim", json={}, status=201)
            rsps.add(responses.GET, f"{API_URL}/v1/incidents/inc-456", json=INCIDENT)
            rsps.add(responses.GET, f"{API_URL}/v1/investigations/inv-123", json={"status": "canceled"})
            assert poller.poll_once(ready_toolset) == 1
            # No /complete registered: a call to it would fail the test.
        chat_function.assert_called_once()

    def test_holmes_failure_is_reported_to_firehydrant(self, poller, ready_toolset, chat_function):
        chat_function.side_effect = RuntimeError("model unavailable")
        with responses.RequestsMock() as rsps:
            _add_pending(rsps, [INVESTIGATION])
            rsps.add(responses.POST, f"{API_URL}/v1/investigations/inv-123/claim", json={}, status=201)
            rsps.add(responses.GET, f"{API_URL}/v1/incidents/inc-456", json=INCIDENT)
            rsps.add(responses.POST, f"{API_URL}/v1/investigations/inv-123/complete", json={}, status=201)
            assert poller.poll_once(ready_toolset) == 1
            body = _request_body(rsps, "/complete")
        assert body["status"] == "failed"
        assert "model unavailable" in body["error"]
        assert "result" not in body

    def test_incident_fetch_failure_falls_back_to_summary(self, poller, ready_toolset, chat_function):
        with responses.RequestsMock() as rsps:
            _add_pending(rsps, [INVESTIGATION])
            rsps.add(responses.POST, f"{API_URL}/v1/investigations/inv-123/claim", json={}, status=201)
            rsps.add(responses.GET, f"{API_URL}/v1/incidents/inc-456", json={"error": "gone"}, status=404)
            rsps.add(responses.GET, f"{API_URL}/v1/investigations/inv-123", json={"status": "in_progress"})
            rsps.add(responses.POST, f"{API_URL}/v1/investigations/inv-123/complete", json={}, status=201)
            assert poller.poll_once(ready_toolset) == 1
            assert _request_body(rsps, "/complete")["status"] == "completed"
        assert "incident #42: Checkout latency" in chat_function.call_args[0][0].ask

    def test_no_pending_investigations(self, poller, ready_toolset, chat_function):
        with responses.RequestsMock() as rsps:
            _add_pending(rsps, [])
            assert poller.poll_once(ready_toolset) == 0
        chat_function.assert_not_called()

    def test_list_failure_does_not_raise(self, poller, ready_toolset):
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{API_URL}/v1/investigations", json={"error": "down"}, status=503)
            assert poller.poll_once(ready_toolset) == 0


class TestPollerToolsetLookup:
    def test_active_when_enabled_and_ready(self, poller, ready_toolset):
        assert poller._active_toolset() is ready_toolset

    def test_inactive_when_toolset_disabled(self, poller, ready_toolset):
        ready_toolset.enabled = False
        assert poller._active_toolset() is None

    def test_inactive_when_toolset_failed(self, poller, ready_toolset):
        ready_toolset.status = ToolsetStatusEnum.FAILED
        ready_toolset.error = "bad key"
        assert poller._active_toolset() is None

    def test_inactive_when_polling_disabled(self, poller, ready_toolset):
        ready_toolset.config = FireHydrantConfig(api_url=API_URL, api_key=API_KEY, poll_investigations=False)
        assert poller._active_toolset() is None

    def test_inactive_when_toolset_not_loaded(self, chat_function):
        config = MagicMock()
        config.cached_tool_executor.toolsets = []
        poller = FireHydrantInvestigationsPoller(config=config, dal=None, chat_function=chat_function)
        assert poller._active_toolset() is None

    def test_builds_executor_when_none_cached(self, ready_toolset, chat_function):
        config = MagicMock()
        config.cached_tool_executor = None
        config.create_server_tool_executor.return_value.toolsets = [ready_toolset]
        poller = FireHydrantInvestigationsPoller(config=config, dal="dal", chat_function=chat_function)
        assert poller._active_toolset() is ready_toolset
        config.create_server_tool_executor.assert_called_once_with("dal")

    def test_start_and_stop(self, poller):
        poller.start()
        assert poller.running
        poller.stop()
        assert not poller.running
