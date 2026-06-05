"""Tests for Kubernetes logs metadata display in various scenarios"""

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams


class TestKubernetesLogsMetadata:
    """Test metadata display for different log scenarios"""

    def setup_method(self):
        """Set up test fixtures"""
        self.toolset = KubernetesLogsToolset()
        # Mock datetime to have consistent output
        self.mock_datetime = datetime(2024, 1, 15, 12, 45, 0, tzinfo=timezone.utc)

    def _create_mock_logs(self, num_logs, content_pattern="Log entry"):
        """Helper to create mock log entries"""
        logs = []
        for i in range(num_logs):
            timestamp = f"2024-01-15T10:30:{i:02d}Z"
            logs.append(f"{timestamp} {content_pattern} {i}")
        return "\n".join(logs)

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_logs_found_and_filtered(self, mock_datetime_module, mock_run):
        """Test: When logs are found and filtered"""
        # Setup datetime mock
        mock_datetime_module.now.return_value = self.mock_datetime

        # Create 1000 logs, 100 with "error", 10 without "INFO"
        logs = []
        for i in range(900):
            logs.append(f"2024-01-15T10:30:{i%60:02d}Z INFO: Normal operation {i}")
        for i in range(100):
            logs.append(f"2024-01-15T10:31:{i%60:02d}Z ERROR: Something went wrong {i}")

        mock_run.return_value = Mock(stdout="\n".join(logs), stderr="", returncode=0)

        params = FetchPodLogsParams(
            namespace="production",
            pod_name="my-app-abc123",
            filter="error",
            exclude_filter="INFO",
            limit=50,
        )

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.SUCCESS
        print("\n=== SCENARIO: Logs found and filtered ===")
        print(result.data)

        # Verify key metadata elements
        assert "LOG QUERY METADATA" in result.data
        assert "Query executed at: 2024-01-15T12:45:00Z (UTC)" in result.data
        assert "Log source: Current and previous container logs" in result.data
        assert "Total logs found before filtering: 2,000" in result.data
        assert "Include filter: 'error'" in result.data
        assert "Matched: 200 logs (10.0% of total)" in result.data
        assert (
            "Display: Showing latest 50 of 200 filtered logs (150 omitted)"
            in result.data
        )

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_hitting_display_limit(self, mock_datetime_module, mock_run):
        """Test: When hitting the display limit"""
        mock_datetime_module.now.return_value = self.mock_datetime

        # Create 5000 error logs
        logs = []
        for i in range(5000):
            logs.append(
                f"2024-01-15T10:{i//100:02d}:{i%60:02d}Z ERROR: Database connection failed {i}"
            )

        mock_run.return_value = Mock(stdout="\n".join(logs), stderr="", returncode=0)

        params = FetchPodLogsParams(
            namespace="production",
            pod_name="database-connector-xyz",
            filter="error",
            limit=100,  # Much lower than available logs
        )

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.SUCCESS
        print("\n=== SCENARIO: Hitting display limit ===")
        print(result.data)

        # Verify display limit warnings
        assert (
            "Display: Showing latest 100 of 10,000 filtered logs (9,900 omitted)"
            in result.data
        )
        assert "⚠️  Hit display limit! Suggestions:" in result.data
        assert "exclude_filter='<pattern1>|<pattern2>|<pattern3>'" in result.data
        assert "filter='<term1>.*<term2>|<exact-phrase>'" in result.data

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_no_logs_match_filter(self, mock_datetime_module, mock_run):
        """Test: When no logs match the filters but logs exist"""
        mock_datetime_module.now.return_value = self.mock_datetime

        # Create 500 INFO logs, no errors
        logs = []
        for i in range(500):
            logs.append(f"2024-01-15T10:30:{i%60:02d}Z INFO: Health check passed {i}")

        mock_run.return_value = Mock(stdout="\n".join(logs), stderr="", returncode=0)

        params = FetchPodLogsParams(
            namespace="production",
            pod_name="healthy-app-def456",
            filter="CRITICAL|FATAL|PANIC",
        )

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.NO_DATA
        print("\n=== SCENARIO: No logs match filter ===")
        print(result.data)

        # Verify no match suggestions
        assert "Result: No logs matched your filters" in result.data
        assert "Total logs found before filtering: 1,000" in result.data
        assert "Matched: 0 logs (0.0% of total)" in result.data
        assert "Try a broader filter pattern" in result.data
        assert "Remove the filter to see all 1,000 available logs" in result.data
        assert "Your filter may be too specific for the log format used" in result.data

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_no_logs_exist_with_time_range(self, mock_datetime_module, mock_run):
        """Test: When no logs exist at all WITH time range specified"""
        # Properly mock datetime
        mock_datetime_module.now.return_value = self.mock_datetime
        mock_datetime_module.fromisoformat = datetime.fromisoformat

        # Return empty logs
        mock_run.return_value = Mock(stdout="", stderr="", returncode=0)

        params = FetchPodLogsParams(
            namespace="staging",
            pod_name="new-deployment-ghi789",
            start_time="2024-01-15T08:00:00Z",
            end_time="2024-01-15T09:30:00Z",  # 1.5 hours duration
        )

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.NO_DATA
        print("\n=== SCENARIO: No logs exist (with time range) ===")
        print(result.data)

        # Verify empty logs suggestions
        assert "Result: No logs found for this pod" in result.data
        assert "Total logs found before filtering: 0" in result.data
        assert "Pod was not running during this time period" in result.data
        assert (
            "Remove time range to see ALL available logs (recommended unless you need this specific timeframe)"
            in result.data
        )
        assert "Or expand time range (e.g., last 24 hours)" in result.data

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_no_logs_exist_without_time_range(self, mock_datetime_module, mock_run):
        """Test: When no logs exist at all WITHOUT time range"""
        mock_datetime_module.now.return_value = self.mock_datetime

        # Return empty logs
        mock_run.return_value = Mock(stdout="", stderr="", returncode=0)

        params = FetchPodLogsParams(namespace="staging", pod_name="nonexistent-pod-xyz")

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.NO_DATA
        print("\n=== SCENARIO: No logs exist (no time range) ===")
        print(result.data)

        # Verify empty logs suggestions
        assert "Result: No logs found for this pod" in result.data
        assert "Total logs found before filtering: 0" in result.data
        assert "Pod may not exist or may have been recently created" in result.data
        assert "Check if pod exists: kubectl get pods -n staging" in result.data
        assert (
            "Check pod events: kubectl describe pod nonexistent-pod-xyz -n staging"
            in result.data
        )
        # Should NOT suggest removing time range
        assert "Remove time range" not in result.data

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_regex_fallback_warnings(self, mock_datetime_module, mock_run):
        """Test: When regex patterns fall back to substring matching"""
        mock_datetime_module.now.return_value = self.mock_datetime

        logs = self._create_mock_logs(100, "ERROR: Failed to process")
        mock_run.return_value = Mock(stdout=logs, stderr="", returncode=0)

        params = FetchPodLogsParams(
            namespace="default",
            pod_name="processor-jkl012",
            filter="invalid[regex",  # Invalid regex
            exclude_filter="also(invalid",  # Another invalid regex
        )

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.NO_DATA
        print("\n=== SCENARIO: Regex fallback warnings ===")
        print(result.data)

        # Verify regex warnings
        assert (
            "⚠️  Filter 'invalid[regex' is not valid regex, using substring match"
            in result.data
        )
        assert (
            "⚠️  Exclude filter 'also(invalid' is not valid regex, using substring match"
            in result.data
        )

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_multi_container_logs(self, mock_datetime_module, mock_run):
        """Test: When pod has multiple containers"""
        mock_datetime_module.now.return_value = self.mock_datetime

        # Create logs with container prefixes
        logs = []
        for i in range(50):
            logs.append(
                f"[api-server-mno345/app] 2024-01-15T10:30:{i:02d}Z INFO: App container log {i}"
            )
            logs.append(
                f"[api-server-mno345/sidecar] 2024-01-15T10:30:{i:02d}Z DEBUG: Sidecar log {i}"
            )

        mock_run.return_value = Mock(stdout="\n".join(logs), stderr="", returncode=0)

        params = FetchPodLogsParams(
            namespace="microservices", pod_name="api-server-mno345"
        )

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.SUCCESS
        print("\n=== SCENARIO: Multi-container logs ===")
        print(result.data)

        # Verify container info
        assert "Container(s): Multiple containers" in result.data
        assert "Display: Showing all 200 logs" in result.data

    @patch("subprocess.run")
    @patch("holmes.plugins.toolsets.kubernetes_logs.datetime")
    def test_exclude_filter_effectiveness(self, mock_datetime_module, mock_run):
        """Test: When exclude filter removes most logs"""
        mock_datetime_module.now.return_value = self.mock_datetime

        # Create 10000 logs, mostly health checks
        logs = []
        for i in range(9900):
            logs.append(f"2024-01-15T10:{i//100:02d}:{i%60:02d}Z GET /health 200 OK")
        for i in range(100):
            logs.append(f"2024-01-15T10:59:{i%60:02d}Z ERROR: Connection timeout")

        mock_run.return_value = Mock(stdout="\n".join(logs), stderr="", returncode=0)

        params = FetchPodLogsParams(
            namespace="production",
            pod_name="api-gateway-pqr678",
            exclude_filter="health|200",
        )

        result = self.toolset.fetch_pod_logs(params)

        assert result.status == StructuredToolResultStatus.SUCCESS
        print("\n=== SCENARIO: Exclude filter effectiveness ===")
        print(result.data)

        # Verify exclude filter stats
        assert "Total logs found before filtering: 20,000" in result.data
        assert "Exclude filter: 'health|200'" in result.data
        assert "Excluded: 19,800 logs" in result.data
        assert "Remaining: 200 logs" in result.data


if __name__ == "__main__":
    # Run the tests and show output
    pytest.main([__file__, "-v", "-s"])
