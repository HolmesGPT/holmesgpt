"""Component tests for the TriggeredHealthCheck (deployment-rollout) trigger.

Pure helpers are tested directly; the execution path is tested with the Kubernetes
API mocked and the rollout-settle wait patched out.
"""

from unittest.mock import MagicMock

import pytest

from holmes_operator import context, trigger_executor
from holmes_operator.config import OperatorConfig
from holmes_operator.models import TriggeredHealthCheckSpec


@pytest.fixture
def mock_config():
    return OperatorConfig(
        holmes_api_url="http://mock-holmes-api:80",
        holmes_api_timeout=300,
        log_level="INFO",
        max_history_items=10,
        cleanup_completed_checks=False,
        completed_check_ttl_hours=24,
    )


@pytest.fixture
def mock_k8s_api():
    api = MagicMock()
    api.create_namespaced_custom_object = MagicMock()
    api.patch_namespaced_custom_object_status = MagicMock()
    api.get_namespaced_custom_object = MagicMock(
        return_value={
            "metadata": {"name": "verify-rollouts", "resourceVersion": "1"},
            "status": {},
        }
    )
    return api


@pytest.fixture
def setup_context(mock_config, mock_k8s_api):
    context.config = mock_config
    context.k8s_api = mock_k8s_api
    trigger_executor.clear_rollout_cache()
    yield
    context.config = None
    context.k8s_api = None
    trigger_executor.clear_rollout_cache()


def _deployment(images, labels=None):
    return {
        "metadata": {"labels": labels or {}},
        "spec": {
            "template": {
                "spec": {"containers": [{"name": "app", "image": i} for i in images]}
            }
        },
    }


class TestHelpers:
    def test_selector_matches_subset(self):
        assert trigger_executor.selector_matches(
            {"app": "checkout"}, {"app": "checkout", "tier": "web"}
        )

    def test_selector_no_match(self):
        assert not trigger_executor.selector_matches(
            {"app": "checkout"}, {"app": "payments"}
        )

    def test_empty_selector_matches_all(self):
        assert trigger_executor.selector_matches({}, {"app": "anything"})

    def test_extract_images_joins_containers(self):
        body = _deployment(["repo/app:v2", "repo/sidecar:v1"])
        assert trigger_executor.extract_images(body) == "repo/app:v2, repo/sidecar:v1"

    def test_render_query_substitutes_tokens(self):
        rendered = trigger_executor.render_query(
            "{{ .deployment }} in {{ .namespace }}: {{ .old.image }} -> {{.new.image}}",
            deployment="checkout",
            namespace="prod",
            old_image="repo/app:v1",
            new_image="repo/app:v2",
        )
        assert rendered == "checkout in prod: repo/app:v1 -> repo/app:v2"

    def test_render_query_unknown_image(self):
        rendered = trigger_executor.render_query(
            "was {{ .old.image }}", "d", "n", "", "repo/app:v2"
        )
        assert rendered == "was unknown"

    def test_compose_query_injects_context_for_terse_query(self):
        # A query with no tokens still gets the rollout facts.
        query = trigger_executor.compose_query(
            "Is the new version healthy?",
            deployment="checkout",
            namespace="prod",
            old_image="repo/app:v1",
            new_image="repo/app:v2",
        )
        assert "Is the new version healthy?" in query
        assert "- Deployment: checkout" in query
        assert "- Namespace: prod" in query
        assert "- Previous image(s): repo/app:v1" in query
        assert "- New image(s): repo/app:v2" in query


class TestDetectRollout:
    def test_baseline_then_change(self):
        key = "prod/checkout"
        # First observation establishes a baseline -> not a rollout
        assert trigger_executor.detect_rollout(key, _deployment(["app:v1"])) is None
        # Same template -> not a rollout
        assert trigger_executor.detect_rollout(key, _deployment(["app:v1"])) is None
        # Template change -> rollout with old/new images
        result = trigger_executor.detect_rollout(key, _deployment(["app:v2"]))
        assert result == ("app:v1", "app:v2")

    def test_forget_resets_baseline(self):
        key = "prod/checkout"
        trigger_executor.detect_rollout(key, _deployment(["app:v1"]))
        trigger_executor.forget_deployment(key)
        # After forgetting, next observation is a baseline again
        assert trigger_executor.detect_rollout(key, _deployment(["app:v2"])) is None


class TestCooldown:
    def test_no_cooldown_when_disabled(self):
        assert not trigger_executor.is_in_cooldown({}, "checkout", 0)

    def test_within_cooldown(self):
        status = {
            "cooldowns": [
                {
                    "deployment": "checkout",
                    "lastTriggerTime": trigger_executor.get_current_time_iso(),
                }
            ]
        }
        assert trigger_executor.is_in_cooldown(status, "checkout", 600)

    def test_outside_cooldown(self):
        status = {
            "cooldowns": [
                {
                    "deployment": "checkout",
                    "lastTriggerTime": "2000-01-01T00:00:00+00:00",
                }
            ]
        }
        assert not trigger_executor.is_in_cooldown(status, "checkout", 600)


class TestPendingQueue:
    def test_due_pending_selects_past_entries(self):
        pending = [
            {"deployment": "a", "fireAt": "2000-01-01T00:00:00+00:00"},
            {"deployment": "b", "fireAt": "2999-01-01T00:00:00+00:00"},
        ]
        due = trigger_executor.due_pending(pending)
        assert [e["deployment"] for e in due] == ["a"]

    def test_compute_fire_at_in_future(self):
        from datetime import datetime, timezone

        fire_at = datetime.fromisoformat(trigger_executor.compute_fire_at(3600))
        assert fire_at > datetime.now(timezone.utc)

    async def test_add_pending_debounces_per_deployment(
        self, setup_context, mock_k8s_api
    ):
        # Resource already has a pending entry for "checkout"
        mock_k8s_api.get_namespaced_custom_object.return_value = {
            "metadata": {"resourceVersion": "1"},
            "status": {
                "pending": [
                    {"deployment": "checkout", "fireAt": "2999-01-01T00:00:00+00:00"}
                ]
            },
        }

        await trigger_executor.add_pending(
            mock_k8s_api,
            trigger_name="verify-rollouts",
            namespace="prod",
            deployment="checkout",
            fire_at="2999-06-01T00:00:00+00:00",
            old_image="app:v1",
            new_image="app:v2",
        )

        patched = mock_k8s_api.patch_namespaced_custom_object_status.call_args[1][
            "body"
        ]["status"]["pending"]
        # Old entry replaced, not duplicated
        assert len(patched) == 1
        assert patched[0]["fireAt"] == "2999-06-01T00:00:00+00:00"
        assert patched[0]["newImage"] == "app:v2"

    async def test_remove_pending_matches_deployment_and_fireat(
        self, setup_context, mock_k8s_api
    ):
        mock_k8s_api.get_namespaced_custom_object.return_value = {
            "metadata": {"resourceVersion": "1"},
            "status": {
                "pending": [
                    {"deployment": "checkout", "fireAt": "2999-01-01T00:00:00+00:00"},
                    {"deployment": "payments", "fireAt": "2999-02-01T00:00:00+00:00"},
                ]
            },
        }

        await trigger_executor.remove_pending(
            mock_k8s_api,
            trigger_name="verify-rollouts",
            namespace="prod",
            entries=[
                {"deployment": "checkout", "fireAt": "2999-01-01T00:00:00+00:00"}
            ],
        )

        patched = mock_k8s_api.patch_namespaced_custom_object_status.call_args[1][
            "body"
        ]["status"]["pending"]
        assert [p["deployment"] for p in patched] == ["payments"]


class TestSpawnCheck:
    async def test_spawns_healthcheck_and_records_status(
        self, setup_context, mock_k8s_api
    ):
        spec = TriggeredHealthCheckSpec(
            deploymentRollout={"selector": {"matchLabels": {"app": "checkout"}}},
            query="checkout rolled out to {{ .new.image }} (was {{ .old.image }})",
            mode="alert",
            destinations=[{"type": "slack", "config": {"channel": "#deploys"}}],
        )

        await trigger_executor.spawn_check(
            trigger_name="verify-rollouts",
            namespace="prod",
            trigger_uid="thc-uid-1",
            spec=spec,
            deployment="checkout",
            old_image="repo/app:v1",
            new_image="repo/app:v2",
            k8s_api=mock_k8s_api,
        )

        # A HealthCheck was created with the rendered query and owner reference
        mock_k8s_api.create_namespaced_custom_object.assert_called_once()
        create_kwargs = mock_k8s_api.create_namespaced_custom_object.call_args[1]
        assert create_kwargs["plural"] == "healthchecks"
        hc = create_kwargs["body"]
        assert hc["kind"] == "HealthCheck"
        query = hc["spec"]["query"]
        # The author's query (with tokens substituted) is present...
        assert "checkout rolled out to repo/app:v2 (was repo/app:v1)" in query
        # ...and the rollout context is auto-injected so a terse query still works.
        assert "- Deployment: checkout" in query
        assert "- Namespace: prod" in query
        assert "- Previous image(s): repo/app:v1" in query
        assert "- New image(s): repo/app:v2" in query
        assert hc["spec"]["mode"] == "alert"
        assert hc["spec"]["destinations"][0]["type"] == "slack"
        owner = hc["metadata"]["ownerReferences"][0]
        assert owner["kind"] == "TriggeredHealthCheck"
        assert owner["uid"] == "thc-uid-1"
        assert hc["metadata"]["labels"]["holmesgpt.dev/triggered-by"] == "verify-rollouts"

        # Status recorded the trigger (history + cooldown + counters)
        mock_k8s_api.patch_namespaced_custom_object_status.assert_called()
        status = mock_k8s_api.patch_namespaced_custom_object_status.call_args[1]["body"][
            "status"
        ]
        assert status["lastTriggerDeployment"] == "checkout"
        assert status["triggerCount"] == 1
        assert status["history"][0]["checkName"].startswith("verify-rollouts-")
        assert status["history"][0]["newImage"] == "repo/app:v2"
        assert status["cooldowns"][0]["deployment"] == "checkout"
