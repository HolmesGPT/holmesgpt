"""Tests for the cleanup of finished HealthChecks (cleanupCompletedChecks).

Kubernetes API calls are mocked using MagicMock.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

from holmes_operator import context
from holmes_operator.cleanup import (
    LIST_PAGE_SIZE,
    cleanup_completed_checks,
    is_expired,
    parse_completion_time,
)
from holmes_operator.config import OperatorConfig
from holmes_operator.scheduler.manager import (
    CLEANUP_INTERVAL_MINUTES,
    CLEANUP_JOB_ID,
    SchedulerManager,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
TTL = timedelta(hours=24)


def make_healthcheck(name, phase, completion_time=None, namespace="default"):
    status = {"phase": phase}
    if completion_time is not None:
        status["completionTime"] = completion_time
    return {
        "metadata": {"name": name, "namespace": namespace, "uid": f"uid-{name}"},
        "status": status,
    }


def hours_ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


class TestParseCompletionTime:
    def test_parses_operator_timestamp(self):
        assert parse_completion_time("2026-08-01T10:00:00.123456+00:00") == datetime(
            2026, 8, 1, 10, 0, 0, 123456, tzinfo=timezone.utc
        )

    def test_parses_z_suffix(self):
        assert parse_completion_time("2026-08-01T10:00:00Z") == datetime(
            2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc
        )

    def test_naive_timestamp_is_utc(self):
        assert parse_completion_time("2026-08-01T10:00:00") == datetime(
            2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize("value", [None, "", "yesterday", 1722506400])
    def test_invalid_values(self, value):
        assert parse_completion_time(value) is None


class TestIsExpired:
    @pytest.mark.parametrize("phase", ["Completed", "Failed"])
    def test_finished_check_older_than_ttl_is_expired(self, phase):
        assert is_expired(make_healthcheck("hc", phase, hours_ago(25)), NOW, TTL)

    @pytest.mark.parametrize("phase", ["Completed", "Failed"])
    def test_finished_check_younger_than_ttl_is_kept(self, phase):
        assert not is_expired(make_healthcheck("hc", phase, hours_ago(23)), NOW, TTL)

    def test_check_exactly_at_ttl_is_expired(self):
        assert is_expired(make_healthcheck("hc", "Completed", hours_ago(24)), NOW, TTL)

    @pytest.mark.parametrize("phase", ["Pending", "Running", None])
    def test_unfinished_check_is_never_expired(self, phase):
        assert not is_expired(make_healthcheck("hc", phase, hours_ago(100)), NOW, TTL)

    def test_check_without_status_is_kept(self):
        assert not is_expired({"metadata": {"name": "hc"}}, NOW, TTL)

    @pytest.mark.parametrize("completion_time", [None, "not-a-timestamp"])
    def test_finished_check_with_unknown_age_is_kept(self, completion_time):
        assert not is_expired(
            make_healthcheck("hc", "Completed", completion_time), NOW, TTL
        )

    def test_completion_time_in_the_future_is_kept(self):
        assert not is_expired(
            make_healthcheck("hc", "Completed", hours_ago(-5)), NOW, TTL
        )


@pytest.fixture
def k8s_api():
    api = MagicMock()
    api.list_cluster_custom_object = MagicMock(return_value={"items": []})
    api.delete_namespaced_custom_object = MagicMock()
    return api


def deleted_names(k8s_api):
    return [
        c.kwargs["name"] for c in k8s_api.delete_namespaced_custom_object.call_args_list
    ]


class TestCleanupCompletedChecks:
    async def test_deletes_only_expired_checks(self, k8s_api):
        k8s_api.list_cluster_custom_object.return_value = {
            "items": [
                make_healthcheck("old-completed", "Completed", hours_ago(30)),
                make_healthcheck("old-failed", "Failed", hours_ago(48), "monitoring"),
                make_healthcheck("recent-completed", "Completed", hours_ago(2)),
                make_healthcheck("old-running", "Running"),
                make_healthcheck("old-pending", "Pending"),
            ]
        }

        deleted = await cleanup_completed_checks(k8s_api, ttl_hours=24, now=NOW)

        assert deleted == 2
        assert deleted_names(k8s_api) == ["old-completed", "old-failed"]
        failed_call = k8s_api.delete_namespaced_custom_object.call_args_list[1]
        assert failed_call.kwargs == {
            "group": "holmesgpt.dev",
            "version": "v1alpha1",
            "namespace": "monitoring",
            "plural": "healthchecks",
            "name": "old-failed",
            "body": {"preconditions": {"uid": "uid-old-failed"}},
        }

    async def test_lists_healthchecks_cluster_wide_in_pages(self, k8s_api):
        k8s_api.list_cluster_custom_object.side_effect = [
            {
                "items": [make_healthcheck("page-1", "Completed", hours_ago(30))],
                "metadata": {"continue": "token-1"},
            },
            {
                "items": [make_healthcheck("page-2", "Failed", hours_ago(30))],
                "metadata": {"continue": ""},
            },
        ]

        deleted = await cleanup_completed_checks(k8s_api, ttl_hours=24, now=NOW)

        assert deleted == 2
        assert deleted_names(k8s_api) == ["page-1", "page-2"]
        first, second = k8s_api.list_cluster_custom_object.call_args_list
        assert first.kwargs == {
            "group": "holmesgpt.dev",
            "version": "v1alpha1",
            "plural": "healthchecks",
            "limit": LIST_PAGE_SIZE,
        }
        assert second.kwargs["_continue"] == "token-1"

    async def test_uses_configured_ttl(self, k8s_api):
        k8s_api.list_cluster_custom_object.return_value = {
            "items": [make_healthcheck("seven-hours", "Completed", hours_ago(7))]
        }

        assert await cleanup_completed_checks(k8s_api, ttl_hours=24, now=NOW) == 0
        assert await cleanup_completed_checks(k8s_api, ttl_hours=6, now=NOW) == 1

    @pytest.mark.parametrize("status", [404, 409])
    async def test_already_deleted_or_replaced_check_is_not_counted(
        self, k8s_api, status
    ):
        k8s_api.list_cluster_custom_object.return_value = {
            "items": [
                make_healthcheck("gone", "Completed", hours_ago(30)),
                make_healthcheck("old", "Completed", hours_ago(30)),
            ]
        }
        k8s_api.delete_namespaced_custom_object.side_effect = [
            ApiException(status=status),
            None,
        ]

        deleted = await cleanup_completed_checks(k8s_api, ttl_hours=24, now=NOW)

        assert deleted == 1
        assert deleted_names(k8s_api) == ["gone", "old"]

    async def test_delete_failure_does_not_stop_the_pass(self, k8s_api):
        k8s_api.list_cluster_custom_object.return_value = {
            "items": [
                make_healthcheck("forbidden", "Completed", hours_ago(30)),
                make_healthcheck("old", "Failed", hours_ago(30)),
            ]
        }
        k8s_api.delete_namespaced_custom_object.side_effect = [
            ApiException(status=403),
            None,
        ]

        deleted = await cleanup_completed_checks(k8s_api, ttl_hours=24, now=NOW)

        assert deleted == 1
        assert deleted_names(k8s_api) == ["forbidden", "old"]

    async def test_list_failure_is_logged_not_raised(self, k8s_api):
        k8s_api.list_cluster_custom_object.side_effect = ApiException(status=500)

        deleted = await cleanup_completed_checks(k8s_api, ttl_hours=24, now=NOW)

        assert deleted == 0
        k8s_api.delete_namespaced_custom_object.assert_not_called()


class TestCleanupScheduling:
    async def test_schedules_periodic_cleanup_job(self, k8s_api):
        manager = SchedulerManager(timezone_str="UTC", k8s_api=k8s_api)

        manager.schedule_completed_check_cleanup(ttl_hours=48)

        job = manager.scheduler.get_job(CLEANUP_JOB_ID)
        assert job is not None
        assert job.func is cleanup_completed_checks
        assert job.args == (k8s_api, 48)
        assert job.trigger.interval == timedelta(minutes=CLEANUP_INTERVAL_MINUTES)
        assert CLEANUP_JOB_ID not in manager.job_registry

    @pytest.mark.parametrize("enabled", [True, False])
    async def test_initialize_schedules_cleanup_only_when_enabled(self, enabled):
        operator_config = OperatorConfig(
            holmes_api_url="http://mock-holmes-api:80",
            holmes_api_timeout=300,
            log_level="INFO",
            max_history_items=10,
            cleanup_completed_checks=enabled,
            completed_check_ttl_hours=6,
        )
        scheduler_manager = MagicMock()
        scheduler_manager.start = AsyncMock()

        with patch.object(
            context.OperatorConfig, "load", return_value=operator_config
        ), patch.object(context.k8s_config, "load_incluster_config"), patch.object(
            context.client, "CustomObjectsApi"
        ), patch.object(context, "HolmesAPIClient"), patch.object(
            context, "SchedulerManager", return_value=scheduler_manager
        ):
            await context.initialize()

        try:
            if enabled:
                scheduler_manager.schedule_completed_check_cleanup.assert_called_once_with(
                    ttl_hours=6
                )
            else:
                scheduler_manager.schedule_completed_check_cleanup.assert_not_called()
        finally:
            context.config = None
            context.api_client = None
            context.k8s_api = None
            context.scheduler_manager = None
