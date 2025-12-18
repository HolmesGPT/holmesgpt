import pytest
from holmes.plugins.toolsets.kubernetes_logs import (
    StructuredLog,
    filter_logs,
    parse_logs,
    KubernetesLogsToolset,
)
from holmes.plugins.toolsets.logging_utils.logging_api import FetchPodLogsParams
from holmes.plugins.toolsets.utils import to_unix_ms


params_dict = {
    "namespace": "default",
    "pod_name": "alertmanager-robusta-kube-prometheus-st-alertmanager-0",
    "start_time": "2025-05-22T04:39:53Z",
    "end_time": "2025-05-23T13:39:53Z",
    "filter": None,
    "limit": None,
}

# init-config-reloader alertmanager config-reloader
# def test_fetch_pod_logs():
#     params = FetchPodLogsParams(**params_dict)

#     toolset = KubernetesLogsToolset()

#     result = toolset.fetch_pod_logs(params)

#     print(result.error or result.data)
#     assert not result.error
#     assert result.data
#     assert "Completed loading of configuration file" in result.data
#     assert False


@pytest.mark.parametrize(
    "logs,container_name,expected",
    [
        # Test 1: None logs
        (None, "test-container", []),
        # Test 2: Empty string logs
        ("", "test-container", []),
        # Test 3: Single line with valid timestamp
        (
            "2023-11-20T10:30:45.123Z Log message here",
            "container1",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45.123Z"),
                    content="Log message here",
                    container="container1",
                )
            ],
        ),
        # Test 4: Multiple lines with timestamps
        (
            "2023-11-20T10:30:45Z First log\n2023-11-20T10:30:46Z Second log",
            "container2",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45Z"),
                    content="First log",
                    container="container2",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:46Z"),
                    content="Second log",
                    container="container2",
                ),
            ],
        ),
        # Test 5: Line without timestamp as first line
        (
            "No timestamp here",
            "container3",
            [
                StructuredLog(
                    timestamp_ms=None,
                    content="No timestamp here",
                    container="container3",
                )
            ],
        ),
        # Test 6: Multi-line log (continuation lines without timestamps)
        (
            "2023-11-20T10:30:45Z Start of log\nContinuation line 1\nContinuation line 2",
            "container4",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45Z"),
                    content="Start of log\nContinuation line 1\nContinuation line 2",
                    container="container4",
                )
            ],
        ),
        # Test 7: Mixed lines with and without timestamps
        (
            "No timestamp first\n2023-11-20T10:30:45Z With timestamp\nAnother continuation",
            "container5",
            [
                StructuredLog(
                    timestamp_ms=None,
                    content="No timestamp first",
                    container="container5",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45Z"),
                    content="With timestamp\nAnother continuation",
                    container="container5",
                ),
            ],
        ),
        # Test 8: Timestamp with timezone offset
        (
            "2023-11-20T10:30:45+05:30 Log with timezone",
            "container6",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T05:00:45Z"),
                    content="Log with timezone",
                    container="container6",
                )
            ],
        ),
        # Test 9: Timestamp with negative timezone offset
        (
            "2023-11-20T10:30:45-08:00 Log with negative timezone",
            "container7",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T18:30:45Z"),
                    content="Log with negative timezone",
                    container="container7",
                )
            ],
        ),
        # Test 10: No container name
        (
            "2023-11-20T10:30:45Z Log without container",
            None,
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45Z"),
                    content="Log without container",
                    container=None,
                )
            ],
        ),
        # Test 11: Multiple continuation lines
        (
            "2023-11-20T10:30:45Z First log\nContinuation 1\nContinuation 2\n2023-11-20T10:30:46Z Second log\nIts continuation",
            "container8",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45Z"),
                    content="First log\nContinuation 1\nContinuation 2",
                    container="container8",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:46Z"),
                    content="Second log\nIts continuation",
                    container="container8",
                ),
            ],
        ),
        # Test 12: Empty lines in logs
        (
            "2023-11-20T10:30:45Z First log\n\n\n2023-11-20T10:30:46Z Second log",
            "container9",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45Z"),
                    content="First log\n\n",
                    container="container9",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:46Z"),
                    content="Second log",
                    container="container9",
                ),
            ],
        ),
        # Test 13: Whitespace after timestamp
        (
            "2023-11-20T10:30:45Z     Log with extra spaces",
            "container10",
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2023-11-20T10:30:45Z"),
                    content="    Log with extra spaces",
                    container="container10",
                )
            ],
        ),
    ],
)
def test_parse_logs_basic(logs, container_name, expected):
    result = parse_logs(logs, container_name)
    assert len(result) == len(expected)
    for i, (res, exp) in enumerate(zip(result, expected)):
        assert res == exp, f"Mismatch at index {i}: {res} != {exp}"


@pytest.mark.parametrize(
    "logs,params,expected_count,expected_contents",
    [
        # Test 1: No filters - all logs returned
        (
            [
                StructuredLog(timestamp_ms=1000, container="app", content="log 1"),
                StructuredLog(timestamp_ms=2000, container="app", content="log 2"),
                StructuredLog(timestamp_ms=3000, container="app", content="log 3"),
            ],
            FetchPodLogsParams(namespace="default", pod_name="test-pod"),
            3,
            ["log 1", "log 2", "log 3"],
        ),
        # Test 2: Match filter only
        (
            [
                StructuredLog(
                    timestamp_ms=1000,
                    container="app",
                    content="error: something failed",
                ),
                StructuredLog(
                    timestamp_ms=2000, container="app", content="info: all good"
                ),
                StructuredLog(
                    timestamp_ms=3000, container="app", content="error: another failure"
                ),
            ],
            FetchPodLogsParams(
                namespace="default", pod_name="test-pod", filter="error"
            ),
            2,
            ["error: something failed", "error: another failure"],
        ),
        # Test 3: Limit filter only
        (
            [
                StructuredLog(timestamp_ms=1000, container="app", content="log 1"),
                StructuredLog(timestamp_ms=2000, container="app", content="log 2"),
                StructuredLog(timestamp_ms=3000, container="app", content="log 3"),
                StructuredLog(timestamp_ms=4000, container="app", content="log 4"),
            ],
            FetchPodLogsParams(namespace="default", pod_name="test-pod", limit=2),
            2,
            ["log 3", "log 4"],  # Last 2 logs
        ),
        # Test 4: Empty logs list
        (
            [],
            FetchPodLogsParams(
                namespace="default", pod_name="test-pod", filter="error"
            ),
            0,
            [],
        ),
        # Test 5: Limit greater than number of logs
        (
            [
                StructuredLog(timestamp_ms=1000, container="app", content="log 1"),
                StructuredLog(timestamp_ms=2000, container="app", content="log 2"),
            ],
            FetchPodLogsParams(namespace="default", pod_name="test-pod", limit=10),
            2,
            ["log 1", "log 2"],
        ),
        # Test 6: Match + Limit
        (
            [
                StructuredLog(timestamp_ms=1000, container="app", content="error 1"),
                StructuredLog(timestamp_ms=2000, container="app", content="info 1"),
                StructuredLog(timestamp_ms=3000, container="app", content="error 2"),
                StructuredLog(timestamp_ms=4000, container="app", content="error 3"),
            ],
            FetchPodLogsParams(
                namespace="default", pod_name="test-pod", filter="error", limit=2
            ),
            2,
            ["error 2", "error 3"],  # Last 2 matching logs
        ),
        # Test 7: Logs with None timestamps
        (
            [
                StructuredLog(
                    timestamp_ms=None, container="app", content="log without timestamp"
                ),
                StructuredLog(
                    timestamp_ms=2000, container="app", content="log with timestamp"
                ),
            ],
            FetchPodLogsParams(namespace="default", pod_name="test-pod"),
            2,
            ["log without timestamp", "log with timestamp"],
        ),
        # Test 8: Unsorted logs should be sorted
        (
            [
                StructuredLog(timestamp_ms=3000, container="app", content="log 3"),
                StructuredLog(timestamp_ms=1000, container="app", content="log 1"),
                StructuredLog(timestamp_ms=2000, container="app", content="log 2"),
            ],
            FetchPodLogsParams(namespace="default", pod_name="test-pod"),
            3,
            ["log 1", "log 2", "log 3"],  # Should be sorted by timestamp
        ),
    ],
)
def test_filter_logs_basic_scenarios(logs, params, expected_count, expected_contents):
    result, _, _, _, _, _ = filter_logs(logs, params)
    assert len(result) == expected_count
    assert [log.content for log in result] == expected_contents


@pytest.mark.parametrize(
    "logs,start_time,end_time,expected_contents",
    [
        # Test time filtering
        (
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-01T12:00:00"),
                    container="app",
                    content="2021-01-01",
                ),  # Jan 1, 2021
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-02T12:00:00"),
                    container="app",
                    content="2021-01-02",
                ),  # Jan 2, 2021
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-03T12:00:00"),
                    container="app",
                    content="2021-01-03",
                ),  # Jan 3, 2021
            ],
            "2021-01-02T00:00:00",
            "2021-01-02T23:59:59",
            ["2021-01-02"],  # Only Jan 2 log
        ),
        # Test with only start time
        (
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-01T12:00:00"),
                    container="app",
                    content="2021-01-01",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-02T12:00:00"),
                    container="app",
                    content="2021-01-02",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-03T12:00:00"),
                    container="app",
                    content="2021-01-03",
                ),
            ],
            "2021-01-02T00:00:00",
            None,
            ["2021-01-02", "2021-01-03"],  # Jan 2 and later
        ),
        # Test with only end time
        (
            [
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-02T22:58:59"),
                    container="app",
                    content="2021-01-02-excluded",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-02T23:30:00"),
                    container="app",
                    content="2021-01-02-match",
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-03T12:00:00"),
                    container="app",
                    content="2021-01-03",
                ),
            ],
            None,
            "2021-01-02T23:59:59",
            [
                "2021-01-02-excluded",
                "2021-01-02-match",
            ],  # With 7-day default, both are included
        ),
        # Test with None timestamp logs and time filter
        (
            [
                StructuredLog(
                    timestamp_ms=None, container="app", content="no timestamp"
                ),
                StructuredLog(
                    timestamp_ms=to_unix_ms("2021-01-02T12:00:00"),
                    container="app",
                    content="2021-01-02",
                ),
            ],
            "2021-01-01T00:00:00",
            "2021-01-03T00:00:00",
            ["no timestamp", "2021-01-02"],  # None timestamp logs are included
        ),
    ],
)
def test_filter_logs_time_filtering(logs, start_time, end_time, expected_contents):
    params = FetchPodLogsParams(
        namespace="default",
        pod_name="test-pod",
        start_time=start_time,
        end_time=end_time,
    )
    result, _, _, _, _, _ = filter_logs(logs, params)
    assert [log.content for log in result] == expected_contents


def test_filter_logs_all_filters_combined():
    logs = [
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-01T12:00:00"),
            container="app",
            content="error: day 1",
        ),
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-02T12:00:00"),
            container="app",
            content="info: day 2",
        ),
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-03T12:00:00"),
            container="app",
            content="error: day 3",
        ),  # expected in result
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-04T12:00:00"),
            container="app",
            content="error: day 4",
        ),  # expected in result
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-05T12:00:00"),
            container="app",
            content="error: day 5",
        ),
    ]

    params = FetchPodLogsParams(
        namespace="default",
        pod_name="test-pod",
        start_time="2021-01-02T00:00:00",
        end_time="2021-01-04T23:59:59",
        filter="error",
        limit=2,
    )

    result, _, _, _, _, _ = filter_logs(logs, params)
    # Should filter by time (days 2-4), then by match (only errors), then limit to last 2
    assert len(result) == 2
    assert [log.content for log in result] == ["error: day 3", "error: day 4"]


def test_filter_logs_edge_case_exact_boundaries():
    # Test logs exactly at time boundaries
    logs = [
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-01T23:59:59"),
            container="app",
            content="just before",
        ),
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-02T00:00:00"),
            container="app",
            content="exactly at start",
        ),
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-03T00:00:00"),
            container="app",
            content="exactly at end",
        ),
        StructuredLog(
            timestamp_ms=to_unix_ms("2021-01-03T00:00:01"),
            container="app",
            content="just after",
        ),
    ]

    params = FetchPodLogsParams(
        namespace="default",
        pod_name="test-pod",
        start_time="2021-01-02T00:00:00",
        end_time="2021-01-03T00:00:00",
    )

    result, _, _, _, _, _ = filter_logs(logs, params)
    # Should include logs at start and end boundaries
    print(result)
    assert [log.content for log in result] == ["exactly at start", "exactly at end"]


class TestParseKubectlLogs:
    """Test the _parse_kubectl_logs method that parses kubectl output with container prefixes"""

    def test_parse_single_container_logs(self):
        toolset = KubernetesLogsToolset()

        # kubectl output with container prefix format: [pod/container] timestamp content
        kubectl_output = """[test-pod/nginx] 2023-05-01T12:00:01Z Starting nginx server
[test-pod/nginx] 2023-05-01T12:00:02Z Server started on port 80
[test-pod/nginx] 2023-05-01T12:00:03Z Ready to accept connections"""

        result = toolset._parse_kubectl_logs(kubectl_output)

        assert len(result.logs) == 3
        assert all(log.container == "nginx" for log in result.logs)
        assert result.logs[0].content == "Starting nginx server"
        assert result.logs[1].content == "Server started on port 80"
        assert result.logs[2].content == "Ready to accept connections"
        assert result.logs[0].timestamp_ms == to_unix_ms("2023-05-01T12:00:01Z")
        assert result.logs[1].timestamp_ms == to_unix_ms("2023-05-01T12:00:02Z")
        assert result.logs[2].timestamp_ms == to_unix_ms("2023-05-01T12:00:03Z")

    def test_parse_multi_container_logs(self):
        toolset = KubernetesLogsToolset()

        # kubectl output with multiple containers
        kubectl_output = """[test-pod/nginx] 2023-05-01T12:00:01Z Nginx starting
[test-pod/sidecar] 2023-05-01T12:00:01Z Sidecar starting
[test-pod/nginx] 2023-05-01T12:00:02Z Nginx ready
[test-pod/sidecar] 2023-05-01T12:00:02Z Sidecar ready"""

        result = toolset._parse_kubectl_logs(kubectl_output)

        assert len(result.logs) == 4
        assert result.logs[0].container == "nginx"
        assert result.logs[1].container == "sidecar"
        assert result.logs[2].container == "nginx"
        assert result.logs[3].container == "sidecar"
        assert result.logs[0].content == "Nginx starting"
        assert result.logs[1].content == "Sidecar starting"

    def test_parse_logs_without_container_prefix(self):
        toolset = KubernetesLogsToolset()

        # Some logs might not have the container prefix (edge case)
        kubectl_output = """2023-05-01T12:00:01Z Log without prefix
[test-pod/nginx] 2023-05-01T12:00:02Z Log with prefix"""

        result = toolset._parse_kubectl_logs(kubectl_output)

        assert len(result.logs) == 2
        assert result.logs[0].container is None
        assert result.logs[0].content == "Log without prefix"
        assert result.logs[1].container == "nginx"
        assert result.logs[1].content == "Log with prefix"

    def test_parse_logs_with_multiline_content(self):
        toolset = KubernetesLogsToolset()

        # Logs with stack traces or multiline content (no timestamp on continuation lines)
        kubectl_output = """[test-pod/app] 2023-05-01T12:00:01Z Error occurred:
[test-pod/app] java.lang.NullPointerException
[test-pod/app]     at com.example.MyClass.method(MyClass.java:42)
[test-pod/app] 2023-05-01T12:00:02Z Continuing execution"""

        result = toolset._parse_kubectl_logs(kubectl_output)

        assert len(result.logs) == 4
        # Lines without timestamps become separate logs with no timestamp
        assert result.logs[0].content == "Error occurred:"
        assert result.logs[0].timestamp_ms == to_unix_ms("2023-05-01T12:00:01Z")
        assert result.logs[1].content == "java.lang.NullPointerException"
        assert result.logs[1].timestamp_ms is None
        assert (
            result.logs[2].content
            == "    at com.example.MyClass.method(MyClass.java:42)"
        )
        assert result.logs[2].timestamp_ms is None
        assert result.logs[3].content == "Continuing execution"
        assert result.logs[3].timestamp_ms == to_unix_ms("2023-05-01T12:00:02Z")

    def test_parse_empty_logs(self):
        toolset = KubernetesLogsToolset()

        result = toolset._parse_kubectl_logs("")
        assert result.logs == []

    def test_parse_logs_with_timezone_offset(self):
        toolset = KubernetesLogsToolset()

        kubectl_output = """[test-pod/app] 2023-05-01T12:00:01+02:00 Log with positive offset
[test-pod/app] 2023-05-01T12:00:01-05:00 Log with negative offset"""

        result = toolset._parse_kubectl_logs(kubectl_output)

        assert len(result.logs) == 2
        assert result.logs[0].content == "Log with positive offset"
        assert result.logs[1].content == "Log with negative offset"
        # Verify timestamps are parsed correctly with timezone offsets
        assert result.logs[0].timestamp_ms == to_unix_ms(
            "2023-05-01T10:00:01Z"
        )  # +02:00 offset
        assert result.logs[1].timestamp_ms == to_unix_ms(
            "2023-05-01T17:00:01Z"
        )  # -05:00 offset

    def test_parse_logs_preserves_whitespace(self):
        """Test that whitespace after timestamps is preserved (except for single space)"""
        toolset = KubernetesLogsToolset()

        # Test various whitespace scenarios
        kubectl_output = """[test-pod/app] 2023-05-01T12:00:01Z     Indented log content
[test-pod/app] 2023-05-01T12:00:02Z	Tab-indented content
[test-pod/app] 2023-05-01T12:00:03Z Normal log content
[test-pod/app] 2023-05-01T12:00:04Z  Two spaces before content"""

        result = toolset._parse_kubectl_logs(kubectl_output)

        assert len(result.logs) == 4
        # Should preserve extra whitespace after removing single space
        assert result.logs[0].content == "    Indented log content"
        assert result.logs[1].content == "\tTab-indented content"  # Tab is preserved
        assert result.logs[2].content == "Normal log content"
        assert (
            result.logs[3].content == " Two spaces before content"
        )  # One space removed, one preserved
