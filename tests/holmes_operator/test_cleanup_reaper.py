"""Unit tests for the completed HealthCheck reaper.

Covers the operator.cleanupCompletedChecks / operator.completedCheckTTLHours
settings: expired terminal checks are deleted, recent/active checks are kept,
and the reaper is a no-op when cleanup is disabled.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from holmes_operator import reaper
from holmes_operator.config import OperatorConfig

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
TTL_HOURS = 24


def make_config(
    cleanup_enabled: bool = True, ttl_hours: int = TTL_HOURS
) -> OperatorConfig:
    """Create a test operator configuration."""
    return OperatorConfig(
        holmes_api_url="http://holmes-api:80",
        holmes_api_timeout=300,
        log_level="INFO",
        max_history_items=10,
        cleanup_completed_checks=cleanup_enabled,
        completed_check_ttl_hours=ttl_hours,
    )


def make_check(
    name: str,
    namespace: str = "default",
    phase: str = "Completed",
    age_hours: float = 48,
    completion_time: object = "__auto__",
) -> dict:
    """Build a HealthCheck CR dict. age_hours is relative to NOW."""
    if completion_time == "__auto__":
        completion_time = (NOW - timedelta(hours=age_hours)).isoformat()
    return {
        "metadata": {"name": name, "namespace": namespace},
        "status": {"phase": phase, "completionTime": completion_time},
    }


class TestIsCheckExpired:
    """Tests for the pure expiry decision."""

    def test_old_completed_check_is_expired(self):
        check = make_check("old", phase="Completed", age_hours=48)
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is True

    def test_old_failed_check_is_expired(self):
        # Failed is terminal and carries completionTime; it must be reaped too.
        check = make_check("old-failed", phase="Failed", age_hours=48)
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is True

    def test_recent_completed_check_is_kept(self):
        check = make_check("recent", phase="Completed", age_hours=1)
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is False

    def test_check_exactly_at_ttl_boundary_is_expired(self):
        check = make_check("boundary", age_hours=TTL_HOURS)
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is True

    @pytest.mark.parametrize("phase", ["Pending", "Running"])
    def test_active_phases_are_never_expired(self, phase):
        check = make_check("active", phase=phase, age_hours=1000)
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is False

    def test_missing_completion_time_is_kept(self):
        check = make_check("no-ts", age_hours=1000, completion_time=None)
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is False

    def test_malformed_completion_time_is_kept(self):
        check = make_check("bad-ts", completion_time="not-a-timestamp")
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is False

    def test_future_completion_time_is_kept(self):
        check = make_check("future", age_hours=-1)
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is False

    def test_missing_status_is_kept(self):
        check = {"metadata": {"name": "no-status", "namespace": "default"}}
        assert reaper.is_check_expired(check, TTL_HOURS, NOW) is False


class TestReapCompletedChecks:
    """Tests for a single cleanup pass against a mocked Kubernetes API."""

    @pytest.fixture
    def mock_k8s_api(self):
        return MagicMock()

    async def test_deletes_expired_and_keeps_recent(self, mock_k8s_api):
        old_completed = make_check("old-completed", phase="Completed", age_hours=72)
        old_failed = make_check("old-failed", phase="Failed", age_hours=30)
        recent = make_check("recent", phase="Completed", age_hours=2)
        running = make_check("running", phase="Running", age_hours=100)
        mock_k8s_api.list_cluster_custom_object.return_value = {
            "items": [old_completed, old_failed, recent, running]
        }

        deleted = await reaper.reap_completed_checks(mock_k8s_api, TTL_HOURS, now=NOW)

        assert deleted == 2
        deleted_names = {
            call.kwargs["name"]
            for call in mock_k8s_api.delete_namespaced_custom_object.call_args_list
        }
        assert deleted_names == {"old-completed", "old-failed"}
        # Deletes are namespaced and target the healthchecks CRD
        for call in mock_k8s_api.delete_namespaced_custom_object.call_args_list:
            assert call.kwargs["namespace"] == "default"
            assert call.kwargs["plural"] == "healthchecks"
            assert call.kwargs["group"] == "holmesgpt.dev"

    async def test_empty_cluster_is_noop(self, mock_k8s_api):
        mock_k8s_api.list_cluster_custom_object.return_value = {"items": []}

        deleted = await reaper.reap_completed_checks(mock_k8s_api, TTL_HOURS, now=NOW)

        assert deleted == 0
        mock_k8s_api.delete_namespaced_custom_object.assert_not_called()

    async def test_concurrent_404_is_tolerated(self, mock_k8s_api):
        gone = make_check("gone", age_hours=72)
        old = make_check("old", age_hours=72)
        mock_k8s_api.list_cluster_custom_object.return_value = {"items": [gone, old]}

        def delete_side_effect(**kwargs):
            if kwargs["name"] == "gone":
                raise ApiException(status=404)
            return {}

        mock_k8s_api.delete_namespaced_custom_object.side_effect = delete_side_effect

        deleted = await reaper.reap_completed_checks(mock_k8s_api, TTL_HOURS, now=NOW)

        assert deleted == 1
        assert mock_k8s_api.delete_namespaced_custom_object.call_count == 2

    async def test_non_404_delete_error_does_not_block_other_deletes(
        self, mock_k8s_api
    ):
        failing = make_check("failing", age_hours=72)
        old = make_check("old", age_hours=72)
        mock_k8s_api.list_cluster_custom_object.return_value = {"items": [failing, old]}

        def delete_side_effect(**kwargs):
            if kwargs["name"] == "failing":
                raise ApiException(status=500)
            return {}

        mock_k8s_api.delete_namespaced_custom_object.side_effect = delete_side_effect

        deleted = await reaper.reap_completed_checks(mock_k8s_api, TTL_HOURS, now=NOW)

        assert deleted == 1
        assert mock_k8s_api.delete_namespaced_custom_object.call_count == 2


class TestReaperLifecycle:
    """Tests for enable/disable gating and the periodic loop."""

    def test_start_reaper_returns_none_when_cleanup_disabled(self):
        task = reaper.start_reaper(MagicMock(), make_config(cleanup_enabled=False))
        assert task is None

    async def test_reaper_loop_runs_pass_then_sleeps(self, monkeypatch):
        mock_k8s_api = MagicMock()
        mock_k8s_api.list_cluster_custom_object.return_value = {
            "items": [make_check("old", age_hours=72)]
        }
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            raise asyncio.CancelledError  # stop the loop after the first pass

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await reaper.reaper_loop(mock_k8s_api, TTL_HOURS, interval_seconds=60)

        mock_k8s_api.list_cluster_custom_object.assert_called_once()
        mock_k8s_api.delete_namespaced_custom_object.assert_called_once()
        assert sleeps == [60]

    async def test_reaper_loop_survives_failed_pass(self, monkeypatch):
        mock_k8s_api = MagicMock()
        mock_k8s_api.list_cluster_custom_object.side_effect = ApiException(status=500)

        async def fake_sleep(seconds):
            raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        # A failing list call must be logged and retried, not kill the task.
        with pytest.raises(asyncio.CancelledError):
            await reaper.reaper_loop(mock_k8s_api, TTL_HOURS, interval_seconds=60)

        mock_k8s_api.delete_namespaced_custom_object.assert_not_called()
