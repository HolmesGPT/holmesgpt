"""Component tests for HealthCheck handler.

These tests verify the HealthCheck handler logic with all external dependencies mocked:
- Holmes API calls are mocked using respx library (for httpx)
- Kubernetes API calls are mocked using MagicMock
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import Response

from holmes_operator import context
from holmes_operator.client.holmes_api_client import HolmesAPIClient
from holmes_operator.config import OperatorConfig
from holmes_operator.handlers.healthcheck import on_healthcheck_create
from holmes_operator.models import CheckPhase, CheckStatus, ConditionStatus


@pytest.fixture
def mock_k8s_api():
    """Create a mocked Kubernetes CustomObjectsApi."""
    api = MagicMock()
    api.patch_namespaced_custom_object_status = MagicMock()

    # Mock get_namespaced_custom_object to return a resource with empty status
    api.get_namespaced_custom_object = MagicMock(
        return_value={
            "metadata": {"name": "test-check", "namespace": "default"},
            "status": {"conditions": []},
        }
    )
    return api


@pytest.fixture
def mock_config():
    """Create test operator configuration."""
    return OperatorConfig(
        holmes_api_url="http://mock-holmes-api:80",
        holmes_api_timeout=300,
        log_level="INFO",
        max_history_items=10,
        cleanup_completed_checks=False,
        completed_check_ttl_hours=24,
    )


@pytest.fixture
async def setup_context(mock_k8s_api, mock_config):
    """Initialize operator context with mocked dependencies."""
    api_client = HolmesAPIClient(
        base_url=mock_config.holmes_api_url,
        timeout=mock_config.holmes_api_timeout,
    )

    # Set context globals directly (avoid loading real k8s config in tests)
    context.config = mock_config
    context.api_client = api_client
    context.k8s_api = mock_k8s_api

    yield

    # Cleanup
    await context.api_client.close()
    context.config = None
    context.api_client = None
    context.k8s_api = None


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    logger = MagicMock()
    return logger


class TestHealthCheckCreate:
    """Tests for on_healthcheck_create handler."""

    @patch("holmes_operator.handlers.healthcheck.kopf.event")
    async def test_successful_check_execution(
        self, mock_event, setup_context, mock_k8s_api, mock_logger, respx_mock
    ):
        """Test successful check execution with pass result."""
        # Mock Holmes API response
        respx_mock.post("http://mock-holmes-api:80/api/checks/execute").mock(
            return_value=Response(
                200,
                json={
                    "status": "pass",
                    "message": "All systems operational",
                    "rationale": "Checked pod status and logs, everything looks good",
                    "duration": 5.2,
                    "model_used": "gpt-4.1",
                    "notifications": [
                        {
                            "type": "slack",
                            "status": "sent",
                            "channel": "#alerts",
                            "error": None,
                        }
                    ],
                },
            )
        )

        # Test data
        spec = {
            "query": "Check if my-app pod is healthy",
            "timeout": 30,
            "mode": "monitor",
            "destinations": [{"type": "slack", "config": {"channel": "#alerts"}}],
        }
        name = "test-check-1"
        namespace = "default"
        uid = "test-uid-123"

        # Execute handler
        await on_healthcheck_create(
            spec=spec,
            name=name,
            namespace=namespace,
            uid=uid,
            logger=mock_logger,
        )

        # Verify status updates were called in correct order
        assert mock_k8s_api.patch_namespaced_custom_object_status.call_count == 4

        # Call 0: Set Pending
        call_0 = mock_k8s_api.patch_namespaced_custom_object_status.call_args_list[0]
        assert call_0[1]["name"] == name
        assert call_0[1]["namespace"] == namespace
        assert call_0[1]["body"]["status"]["phase"] == CheckPhase.PENDING.value

        # Call 1: Set Running
        call_1 = mock_k8s_api.patch_namespaced_custom_object_status.call_args_list[1]
        assert call_1[1]["body"]["status"]["phase"] == CheckPhase.RUNNING.value

        # Call 2: Set Completed
        call_2 = mock_k8s_api.patch_namespaced_custom_object_status.call_args_list[2]
        status = call_2[1]["body"]["status"]
        assert status["phase"] == CheckPhase.COMPLETED.value
        assert status["result"] == CheckStatus.PASS.value
        assert status["message"] == "All systems operational"
        assert (
            status["rationale"] == "Checked pod status and logs, everything looks good"
        )
        assert status["duration"] == 5.2
        assert status["modelUsed"] == "gpt-4.1"
        assert len(status["notifications"]) == 1
        assert status["notifications"][0]["type"] == "slack"

        # Call 3: Add Condition
        call_3 = mock_k8s_api.patch_namespaced_custom_object_status.call_args_list[3]
        conditions = call_3[1]["body"]["status"]["conditions"]
        assert len(conditions) > 0
        assert conditions[0]["type"] == "Complete"
        assert conditions[0]["status"] == ConditionStatus.TRUE.value

    @patch("holmes_operator.handlers.healthcheck.kopf.event")
    async def test_failed_check_execution(
        self, mock_event, setup_context, mock_k8s_api, mock_logger, respx_mock
    ):
        """Test check execution with fail result."""
        # Mock Holmes API response
        respx_mock.post("http://mock-holmes-api:80/api/checks/execute").mock(
            return_value=Response(
                200,
                json={
                    "status": "fail",
                    "message": "Pod is in CrashLoopBackOff state",
                    "rationale": "Container exits with error code 1. Logs show OOMKilled.",
                    "duration": 3.8,
                    "model_used": "gpt-4.1",
                    "notifications": [
                        {
                            "type": "slack",
                            "status": "skipped",
                            "channel": None,
                            "error": "SLACK_TOKEN not configured",
                        }
                    ],
                },
            )
        )

        spec = {
            "query": "Check if my-app pod is healthy",
            "timeout": 30,
            "mode": "alert",
        }
        name = "test-check-2"
        namespace = "default"
        uid = "test-uid-456"

        await on_healthcheck_create(
            spec=spec,
            name=name,
            namespace=namespace,
            uid=uid,
            logger=mock_logger,
        )

        # Verify final status is Completed with fail result
        assert mock_k8s_api.patch_namespaced_custom_object_status.call_count == 4

        # Call 2: Set Completed with fail result
        call_2 = mock_k8s_api.patch_namespaced_custom_object_status.call_args_list[2]
        status = call_2[1]["body"]["status"]
        assert status["phase"] == CheckPhase.COMPLETED.value
        assert status["result"] == CheckStatus.FAIL.value
        assert "CrashLoopBackOff" in status["message"]
        assert status["modelUsed"] == "gpt-4.1"
        assert len(status["notifications"]) == 1
        assert status["notifications"][0]["type"] == "slack"
        assert status["notifications"][0]["status"] == "skipped"

    @patch("holmes_operator.handlers.healthcheck.kopf.event")
    async def test_api_error_handling(
        self, mock_event, setup_context, mock_k8s_api, mock_logger, respx_mock
    ):
        """Test handling of Holmes API errors."""
        # Mock Holmes API error response
        respx_mock.post("http://mock-holmes-api:80/api/checks/execute").mock(
            return_value=Response(
                500,
                json={"detail": "Internal server error"},
            )
        )

        spec = {
            "query": "Check if my-app pod is healthy",
            "timeout": 30,
            "mode": "monitor",
        }
        name = "test-check-3"
        namespace = "default"
        uid = "test-uid-789"

        # The handler re-raises exceptions for kopf to handle
        with pytest.raises(Exception):
            await on_healthcheck_create(
                spec=spec,
                name=name,
                namespace=namespace,
                uid=uid,
                logger=mock_logger,
            )

        # Verify status was set to Failed (after retries)
        # Should have: Pending -> Running -> Failed -> Add Condition
        assert mock_k8s_api.patch_namespaced_custom_object_status.call_count == 4

        # Call 2: Set Failed
        call_2 = mock_k8s_api.patch_namespaced_custom_object_status.call_args_list[2]
        status = call_2[1]["body"]["status"]
        assert status["phase"] == CheckPhase.FAILED.value
        assert status["result"] == CheckStatus.ERROR.value
