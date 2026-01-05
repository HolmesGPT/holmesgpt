from unittest.mock import Mock, patch

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams


class TestKubernetesLogsNoData:
    """Test that kubernetes logs returns NO_DATA status when appropriate"""

    @patch("subprocess.run")
    def test_no_logs_returns_no_data_status(self, mock_run):
        """Test that NO_DATA status is returned when pod has no logs"""
        # Mock kubectl returning empty output (no logs)
        mock_run.return_value = Mock(
            returncode=0,
            stdout="",  # Empty logs
            stderr="",
        )

        toolset = KubernetesLogsToolset()
        params = FetchPodLogsParams(pod_name="test-pod", namespace="test-namespace")

        result = toolset.fetch_pod_logs(params)

        # Should return NO_DATA status when there are no logs
        assert result.status == StructuredToolResultStatus.NO_DATA
        # May include metadata about the query even when no logs found

    @patch("subprocess.run")
    def test_logs_with_filter_no_matches_returns_no_data(self, mock_run):
        """Test that NO_DATA status is returned when logs exist but filter matches nothing"""
        # Mock kubectl returning logs that won't match our filter
        mock_run.return_value = Mock(
            returncode=0,
            stdout="[test-pod/app] 2024-01-01T10:00:00Z INFO Application started\n"
            "[test-pod/app] 2024-01-01T10:00:01Z INFO Health check passed",
            stderr="",
        )

        toolset = KubernetesLogsToolset()
        params = FetchPodLogsParams(
            pod_name="test-pod",
            namespace="test-namespace",
            filter="ERROR",  # Filter that won't match any logs
        )

        result = toolset.fetch_pod_logs(params)

        # Should return NO_DATA when filter matches no logs
        assert result.status == StructuredToolResultStatus.NO_DATA

    @patch("subprocess.run")
    def test_logs_with_time_range_no_matches_returns_no_data(self, mock_run):
        """Test that NO_DATA status is returned when logs exist but outside time range"""
        # Mock kubectl returning logs from 2024
        mock_run.return_value = Mock(
            returncode=0,
            stdout="[test-pod/app] 2024-01-01T10:00:00Z INFO Old log entry",
            stderr="",
        )

        toolset = KubernetesLogsToolset()
        params = FetchPodLogsParams(
            pod_name="test-pod",
            namespace="test-namespace",
            start_time="2025-01-01T00:00:00Z",  # Future time range
            end_time="2025-01-02T00:00:00Z",
        )

        result = toolset.fetch_pod_logs(params)

        # Should return NO_DATA when no logs in time range
        assert result.status == StructuredToolResultStatus.NO_DATA

    @patch("subprocess.run")
    def test_logs_exist_returns_success_with_metadata(self, mock_run):
        """Test that SUCCESS status is returned when logs exist, even with metadata"""
        # Mock kubectl returning actual logs
        mock_run.return_value = Mock(
            returncode=0,
            stdout="[test-pod/app] 2025-01-01T10:00:00Z ERROR Something went wrong",
            stderr="",
        )

        toolset = KubernetesLogsToolset()
        params = FetchPodLogsParams(pod_name="test-pod", namespace="test-namespace")

        result = toolset.fetch_pod_logs(params)

        # Should return SUCCESS when logs exist
        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data is not None and len(result.data) > 0
        # Should contain the log content
        assert "Something went wrong" in result.data
        # Should contain metadata
        assert "LOG QUERY METADATA" in result.data
