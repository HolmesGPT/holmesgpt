import json
import unittest
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams


def pod_spec(cmd, containers, *, init=(), ephemeral=()):
    return CompletedProcess(
        cmd,
        0,
        json.dumps(
            {
                "spec": {
                    field: [{"name": name} for name in names]
                    for field, names in (
                        ("containers", containers),
                        ("initContainers", init),
                        ("ephemeralContainers", ephemeral),
                    )
                }
            }
        ),
        "",
    )


def mock_subprocess_run(cmd, **kwargs):
    """Mock subprocess.run for kubectl commands"""
    print(f"Mock subprocess.run called with: {' '.join(cmd)}")

    if cmd[0] == "kubectl" and cmd[1] == "version":
        # Health check
        result = MagicMock()
        result.returncode = 0
        result.stdout = "Client Version: v1.27.0"
        result.stderr = ""
        return result

    if cmd[:3] == ["kubectl", "get", "pod"]:
        if cmd[3] == "nonexistent-pod":
            return CompletedProcess(cmd, 1, "", 'pods "nonexistent-pod" not found')
        containers = (
            ["container1", "container2"]
            if cmd[3] == "multi-container-pod"
            else ["my-container"]
        )
        return pod_spec(cmd, containers)

    if cmd[0] == "kubectl" and cmd[1] == "logs":
        pod_name = cmd[2]
        namespace = cmd[4]  # After -n flag
        previous = "--previous" in cmd

        result = MagicMock()

        if namespace == "default" and pod_name == "test-pod":
            result.returncode = 0
            result.stderr = None

            # kubectl with --prefix=true and --all-containers=true formats output as:
            # [pod/container] timestamp content
            if previous:
                result.stdout = (
                    "[test-pod/my-container] 2023-05-01T12:00:01Z Log line 1 - prev - container=my-container\n"
                    "[test-pod/my-container] 2023-05-01T12:00:02Z Log line 2 - prev - container=my-container\n"
                    "[test-pod/my-container] 2023-05-01T12:00:03Z Log line 3 - prev - container=my-container\n"
                )
            else:
                result.stdout = (
                    "[test-pod/my-container] 2023-05-01T12:00:04Z Log line 1 - current - container=my-container\n"
                    "[test-pod/my-container] 2023-05-01T12:00:05Z Log line 2 - current - container=my-container\n"
                    "[test-pod/my-container] 2023-05-01T12:00:06Z Log line 3 - current - container=my-container\n"
                )
        elif namespace == "default" and pod_name == "multi-container-pod":
            result.returncode = 0
            result.stderr = None

            # Multi-container output with interleaved logs
            if previous:
                result.stdout = (
                    "[multi-container-pod/container1] 2023-05-01T12:00:01Z Log line 1 - prev - container=container1\n"
                    "[multi-container-pod/container2] 2023-05-01T12:00:01Z Log line 1 - prev - container=container2\n"
                    "[multi-container-pod/container1] 2023-05-01T12:00:02Z Log line 2 - prev - container=container1\n"
                    "[multi-container-pod/container2] 2023-05-01T12:00:02Z Log line 2 - prev - container=container2\n"
                    "[multi-container-pod/container1] 2023-05-01T12:00:03Z Log line 3 - prev - container=container1\n"
                    "[multi-container-pod/container2] 2023-05-01T12:00:03Z Log line 3 - prev - container=container2\n"
                )
            else:
                result.stdout = (
                    "[multi-container-pod/container1] 2023-05-01T12:00:04Z Log line 1 - current - container=container1\n"
                    "[multi-container-pod/container2] 2023-05-01T12:00:04Z Log line 1 - current - container=container2\n"
                    "[multi-container-pod/container1] 2023-05-01T12:00:05Z Log line 2 - current - container=container1\n"
                    "[multi-container-pod/container2] 2023-05-01T12:00:05Z Log line 2 - current - container=container2\n"
                    "[multi-container-pod/container1] 2023-05-01T12:00:06Z Log line 3 - current - container=container1\n"
                    "[multi-container-pod/container2] 2023-05-01T12:00:06Z Log line 3 - current - container=container2\n"
                )
        elif namespace == "default" and pod_name == "nonexistent-pod":
            result.returncode = 1
            result.stdout = ""
            result.stderr = (
                'Error from server (NotFound): pods "nonexistent-pod" not found'
            )
        else:
            raise Exception(
                f"Unexpected mock call: pod={pod_name}, namespace={namespace}"
            )

        if previous:
            container = cmd[cmd.index("-c") + 1]
            result.stdout = "".join(
                line
                for line in result.stdout.splitlines(keepends=True)
                if line.startswith(f"[{pod_name}/{container}]")
            )
        return result

    raise Exception(f"Unexpected command: {' '.join(cmd)}")


class TestKubernetesLogsToolset(unittest.TestCase):
    def setUp(self):
        # Patch subprocess.run for all tests
        self.subprocess_patcher = patch(
            "subprocess.run", side_effect=mock_subprocess_run
        )
        self.mock_subprocess = self.subprocess_patcher.start()
        self.addCleanup(self.subprocess_patcher.stop)

        # Create toolset after patching
        self.toolset = KubernetesLogsToolset()

    def test_single_container(self):
        params = FetchPodLogsParams(
            namespace="default",
            pod_name="test-pod",
            limit=2000,
            start_time=None,
            end_time=None,
            filter=None,
        )

        result = self.toolset.fetch_pod_logs(params=params)

        self.assertEqual(result.status, StructuredToolResultStatus.SUCCESS)
        self.assertEqual(result.return_code, 0)
        self.assertIsNone(result.error)
        assert result.data

        # Just check that logs contain the expected content
        assert "Log line 1 - prev - container=my-container" in result.data
        assert "Log line 2 - prev - container=my-container" in result.data
        assert "Log line 3 - prev - container=my-container" in result.data
        assert "Log line 1 - current - container=my-container" in result.data
        assert "Log line 2 - current - container=my-container" in result.data
        assert "Log line 3 - current - container=my-container" in result.data

        # Check metadata is present
        assert "LOG QUERY METADATA" in result.data
        assert "Total logs found before filtering: 6" in result.data

    def test_multi_containers(self):
        """Test multi-container pod logs with container prefixes"""

        params = FetchPodLogsParams(
            namespace="default",
            pod_name="multi-container-pod",
            limit=2000,
            start_time=None,
            end_time=None,
            filter=None,
        )

        result = self.toolset.fetch_pod_logs(params=params)

        self.assertEqual(result.status, StructuredToolResultStatus.SUCCESS)
        self.assertEqual(result.return_code, 0)
        self.assertIsNone(result.error)

        print(result.data)

        # Verify that the logs are formatted with container prefixes for multi-container pods
        assert "container1: Log line 1 - prev - container=container1" in result.data
        assert "container2: Log line 1 - prev - container=container2" in result.data
        assert "container1: Log line 2 - prev - container=container1" in result.data
        assert "container2: Log line 2 - prev - container=container2" in result.data
        assert "container1: Log line 3 - prev - container=container1" in result.data
        assert "container2: Log line 3 - prev - container=container2" in result.data
        assert "container1: Log line 1 - current - container=container1" in result.data
        assert "container2: Log line 1 - current - container=container2" in result.data
        assert "container1: Log line 2 - current - container=container1" in result.data
        assert "container2: Log line 2 - current - container=container2" in result.data
        assert "container1: Log line 3 - current - container=container1" in result.data
        assert "container2: Log line 3 - current - container=container2" in result.data

        # Check metadata is present
        assert "LOG QUERY METADATA" in result.data
        assert "Total logs found before filtering: 12" in result.data
        assert "Container(s): Multiple containers" in result.data

    def test_previous_logs_survive_a_container_without_previous_logs(self):
        missing_previous = (
            'previous terminated container "sidecar" in pod "test-pod" not found'
        )
        previous_log = "[test-pod/app] 2023-05-01T12:00:01Z startup failed\n"
        current_logs = (
            "[test-pod/app] 2023-05-01T12:00:02Z restarted\n"
            "[test-pod/sidecar] 2023-05-01T12:00:03Z running\n"
        )

        def kubectl(cmd, **kwargs):
            if cmd[:3] == ["kubectl", "get", "pod"]:
                return pod_spec(cmd, ["app", "sidecar"])
            if "--previous" not in cmd:
                return CompletedProcess(cmd, 0, current_logs, "")
            if cmd[cmd.index("-c") + 1] == "app":
                return CompletedProcess(cmd, 0, previous_log, "")
            return CompletedProcess(
                cmd, 1, "", f"Error from server (BadRequest): {missing_previous}\n"
            )

        self.mock_subprocess.side_effect = kubectl
        result = self.toolset.fetch_pod_logs(
            FetchPodLogsParams(namespace="default", pod_name="test-pod")
        )

        self.assertEqual(result.status, StructuredToolResultStatus.SUCCESS)
        self.assertEqual(result.return_code, 0)
        self.assertIsNone(result.error)
        self.assertIn("app: startup failed", result.data)
        self.assertIn("app: restarted", result.data)
        self.assertIn("sidecar: running", result.data)
        self.assertIn("Total logs found before filtering: 3", result.data)
        self.assertNotIn(missing_previous, result.data)

    def test_unterminated_previous_logs_and_fetch_errors_stay_separate(self):
        previous_log = "[pod/test-pod/app] 2023-05-01T12:00:01Z startup failed"
        sidecar_error = "node did not return the sidecar log"
        current_log = "[pod/test-pod/app] 2023-05-01T12:00:02Z restarted\n"

        def kubectl(cmd, **kwargs):
            if cmd[:3] == ["kubectl", "get", "pod"]:
                return pod_spec(
                    cmd, ["app", "sidecar"], init=["setup"], ephemeral=["debug"]
                )
            if "--previous" not in cmd:
                return CompletedProcess(cmd, 0, current_log, "")
            if "--all-containers=true" in cmd:
                return CompletedProcess(
                    cmd, 0, f"{previous_log}error: {sidecar_error}\n", ""
                )
            container = cmd[cmd.index("-c") + 1]
            if container == "app":
                return CompletedProcess(cmd, 0, previous_log, "")
            if container == "sidecar":
                return CompletedProcess(cmd, 1, "", sidecar_error)
            if container == "setup":
                return CompletedProcess(
                    cmd, 0, "[pod/test-pod/setup] 2023-05-01T12:00:00Z prepared", ""
                )
            self.assertEqual(container, "debug")
            return CompletedProcess(
                cmd,
                1,
                "[pod/test-pod/debug] 2023-05-01T12:00:01Z partial debug logs",
                "connection closed",
            )

        self.mock_subprocess.side_effect = kubectl
        result = self.toolset.fetch_pod_logs(
            FetchPodLogsParams(namespace="default", pod_name="test-pod")
        )

        self.assertEqual(result.status, StructuredToolResultStatus.SUCCESS)
        self.assertIn("app: startup failed\n", result.data)
        self.assertIn("app: restarted", result.data)
        self.assertIn("setup: prepared", result.data)
        self.assertIn("debug: partial debug logs", result.data)
        self.assertIn(f"sidecar: {sidecar_error}", result.data)
        self.assertIn("debug: connection closed", result.data)
        self.assertIn("Total logs found before filtering: 4", result.data)

    def test_empty_logs_distinguish_expected_and_unexpected_errors(self):
        missing_previous = (
            'Error from server (BadRequest): previous terminated container "app" '
            'in pod "test-pod" not found'
        )
        warning = "Warning: test API warning\n"
        denied = "Error from server (Forbidden): access denied"
        cases = [
            (missing_previous, StructuredToolResultStatus.NO_DATA),
            (warning + missing_previous, StructuredToolResultStatus.NO_DATA),
            (warning + denied, StructuredToolResultStatus.ERROR),
            (missing_previous + "\n" + denied, StructuredToolResultStatus.ERROR),
        ]
        for error, expected_status in cases:
            with self.subTest(error=error):

                def kubectl(cmd, **kwargs):
                    if cmd[:3] == ["kubectl", "get", "pod"]:
                        return pod_spec(cmd, ["app"])
                    if "--previous" in cmd:
                        return CompletedProcess(cmd, 1, "", error)
                    return CompletedProcess(cmd, 0, "", "")

                self.mock_subprocess.side_effect = kubectl
                result = self.toolset.fetch_pod_logs(
                    FetchPodLogsParams(namespace="default", pod_name="test-pod")
                )

                self.assertEqual(result.status, expected_status)
                if expected_status == StructuredToolResultStatus.ERROR:
                    self.assertIn(f"Previous logs: app: {error}", result.error)
                else:
                    self.assertIsNone(result.error)
                    self.assertNotIn(error, result.data)

    def test_pod_not_found(self):
        """Test error handling when pod is not found"""
        params = FetchPodLogsParams(
            namespace="default",
            pod_name="nonexistent-pod",
            limit=2000,
            start_time=None,
            end_time=None,
            filter=None,
        )

        result = self.toolset.fetch_pod_logs(params=params)

        # With kubectl, we get an ERROR status when pod is not found
        self.assertEqual(result.return_code, 1)
        self.assertEqual(result.status, StructuredToolResultStatus.ERROR)
        self.assertIn("not found", result.error)

    def test_filter_logs(self):
        params = FetchPodLogsParams(
            namespace="default",
            pod_name="test-pod",  # This will use our standard test logs
            limit=2000,
            start_time=None,
            end_time=None,
            filter="line 2",  # Filter for logs containing "line 2"
        )

        result = self.toolset.fetch_pod_logs(params=params)

        self.assertEqual(result.status, StructuredToolResultStatus.SUCCESS)
        self.assertEqual(result.return_code, 0)
        self.assertIsNone(result.error)

        assert result.data
        print(f"ACTUAL:\n{result.data}")

        # Should only contain logs with "line 2"
        self.assertIn("Log line 2", result.data)
        self.assertNotIn("Log line 1", result.data)
        self.assertNotIn("Log line 3", result.data)

    def test_limit_logs(self):
        """Test that limit parameter works and includes metadata"""
        params = FetchPodLogsParams(
            namespace="default",
            pod_name="test-pod",
            start_time=None,
            end_time=None,
            limit=2,  # Limit to 2 logs
        )

        result = self.toolset.fetch_pod_logs(params=params)

        self.assertEqual(result.status, StructuredToolResultStatus.SUCCESS)
        self.assertEqual(result.return_code, 0)
        self.assertIsNone(result.error)

        assert result.data
        print(f"ACTUAL:\n{result.data}")

        # Should have metadata about total vs limited
        self.assertIn("LOG QUERY METADATA", result.data)
        self.assertIn("Total logs found before filtering: 6", result.data)
        self.assertIn("Display: Showing latest 2 of 6", result.data)

        # Count actual log lines (before metadata section)
        lines = result.data.split("\n")
        metadata_start = None
        for i, line in enumerate(lines):
            if "=" * 80 in line:  # Look for the separator before metadata
                metadata_start = i
                break

        # Count non-empty lines before metadata
        if metadata_start is not None:
            log_lines = [line for line in lines[:metadata_start] if line.strip()]
            self.assertEqual(len(log_lines), 2)


if __name__ == "__main__":
    unittest.main()
