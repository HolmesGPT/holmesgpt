import pytest
from dateutil import parser  # type: ignore
from freezegun import freeze_time

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Toolset,
    ToolsetStatusEnum,
)
from holmes.core.tools_utils.toolset_utils import filter_out_default_logging_toolset
from holmes.plugins.toolsets.logging_utils.logging_api import (
    BasePodLoggingToolset,
    FetchPodLogsParams,
)
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_rfc3339,
    to_unix_ms,
    toolset_name_for_one_liner,
)


@freeze_time("2020-09-14T13:50:40Z")
@pytest.mark.parametrize(
    "start_timestamp, end_timestamp, expected_start, expected_end",
    [
        (
            None,
            None,
            "2020-09-14T12:50:40Z",
            "2020-09-14T13:50:40Z",
        ),
        (
            -7200,
            0,  # alias for now()
            "2020-09-14T11:50:40Z",
            "2020-09-14T13:50:40Z",
        ),
        (
            -7200,  # always relative to end
            -1800,  # relative to now() when negative
            "2020-09-14T11:20:40Z",
            "2020-09-14T13:20:40Z",
        ),
        # Integer timestamps
        (
            1600000000,
            1600003600,
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
        ),
        # RFC3339 formatted timestamps
        (
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
        ),
        # Negative start integer as relative time to Unix
        (
            -3600,
            1600003600,
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
        ),
        # Negative start integer as relative time to RFC3339
        (
            -300,
            "2020-09-13T12:26:40Z",
            "2020-09-13T12:21:40Z",
            "2020-09-13T12:26:40Z",
        ),
        # Auto inversion, Negative end integer as relative time to RFC3339
        (
            "2020-09-13T12:26:40Z",
            -300,
            "2020-09-13T12:21:40Z",
            "2020-09-13T12:26:40Z",
        ),
        # Auto-inversion if start is after end
        (
            1600003600,
            1600000000,
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
        ),
        # String integers
        (
            "1600000000",
            "1600003600",
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
        ),
        # Mixed format (RFC3339 + Unix)
        (
            "2020-09-13T12:26:40Z",
            1600003600,
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
        ),
        # Mixed format (Unix + RFC3339)
        (
            1600000000,
            "2020-09-13T13:26:40Z",
            "2020-09-13T12:26:40Z",
            "2020-09-13T13:26:40Z",
        ),
    ],
)
def test_process_timestamps_to_rfc3339(
    start_timestamp, end_timestamp, expected_start, expected_end
):
    result_start, result_end = process_timestamps_to_rfc3339(
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        default_time_span_seconds=3600,
    )

    # For time-dependent tests, we allow a small tolerance
    if start_timestamp is None or end_timestamp is None:
        # Parse the times to compare them within a small tolerance
        result_start_dt = parser.parse(result_start)
        result_end_dt = parser.parse(result_end)
        expected_start_dt = parser.parse(expected_start)
        expected_end_dt = parser.parse(expected_end)

        # Allow 2 seconds tolerance for current time comparisons
        assert abs((result_start_dt - expected_start_dt).total_seconds()) < 2
        assert abs((result_end_dt - expected_end_dt).total_seconds()) < 2
    else:
        assert result_start == expected_start
        assert result_end == expected_end


@pytest.mark.parametrize(
    "date_time_str, expected_timestamp",
    [
        ("2023-11-20T10:30:45.123Z", 1700476245123),
        ("2023-11-20T10:30:45.123+00:00", 1700476245123),
        ("2023-11-20T08:30:45.123-02:00", 1700476245123),
        ("2023-11-20T12:30:45.123+02:00", 1700476245123),
        ("2023-11-20T19:00:45.123+08:30", 1700476245123),
        ("2023-11-20T02:00:45.123-08:30", 1700476245123),
    ],
)
def test_to_unix_ms(date_time_str, expected_timestamp):
    assert to_unix_ms(date_time_str) == expected_timestamp


class DummyNonLoggingToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="non_logging_toolset", description="Dummy toolset", tools=[]
        )

    def _invoke(
        self, params: dict, user_approved: bool = False
    ) -> StructuredToolResult:
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS)

    def get_parameterized_one_liner(self, params: dict) -> str:
        """Generate a one-line description of this tool invocation"""
        namespace = params.get("namespace", "unknown-namespace")
        pod_name = params.get("pod_name", "unknown-pod")
        return f"Fetching logs for pod {pod_name} in namespace {namespace}"

    def get_example_config(self):
        return {}


class DummyLoggingToolset(BasePodLoggingToolset):
    def __init__(self, name, enabled: bool = True):
        super().__init__(name=name, description=name, tools=[])
        if enabled:
            self.status = ToolsetStatusEnum.ENABLED

    @property
    def supported_capabilities(self) -> set:
        return set()  # No advanced capabilities for dummy toolset

    def fetch_pod_logs(self, params: FetchPodLogsParams) -> StructuredToolResult:
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS)

    def get_example_config(self):
        return {}


@pytest.mark.parametrize(
    "unfiltered_toolsets, expected_toolsets",
    [
        # non logging toolsets are not filtered
        ([DummyNonLoggingToolset()], [DummyNonLoggingToolset()]),
        # disabled toolsets are returned as-is
        (
            [DummyLoggingToolset(name="toolset1", enabled=False)],
            [DummyLoggingToolset(name="toolset1", enabled=False)],
        ),
        (
            [
                DummyLoggingToolset(name="toolset1"),
                DummyLoggingToolset(name="toolset2", enabled=False),
            ],
            [
                DummyLoggingToolset(name="toolset1"),
                DummyLoggingToolset(name="toolset2", enabled=False),
            ],
        ),
        # kubernetes/logs is never favoured
        (
            [DummyLoggingToolset(name="kubernetes/logs")],
            [DummyLoggingToolset(name="kubernetes/logs")],
        ),
        (
            [
                DummyLoggingToolset(name="kubernetes/logs"),
                DummyLoggingToolset(name="toolset1"),
            ],
            [DummyLoggingToolset(name="toolset1")],
        ),
        (
            [
                DummyLoggingToolset(name="kubernetes/logs"),
                DummyLoggingToolset(name="toolset1", enabled=False),
            ],
            [
                DummyLoggingToolset(name="kubernetes/logs"),
                DummyLoggingToolset(name="toolset1", enabled=False),
            ],
        ),
        # enabled toolset is favoured in alphabetical order
        (
            [
                DummyLoggingToolset(name="toolset1"),
                DummyLoggingToolset(name="toolset2"),
            ],
            [
                DummyLoggingToolset(name="toolset1"),
            ],
        ),
        (
            [
                DummyLoggingToolset(name="toolset2"),
                DummyLoggingToolset(name="toolset1"),
            ],
            [
                DummyLoggingToolset(name="toolset1"),
            ],
        ),
    ],
)
def test_filter_out_default_toolset(unfiltered_toolsets, expected_toolsets):
    filtered_toolsets = filter_out_default_logging_toolset(unfiltered_toolsets)

    assert len(filtered_toolsets) == len(expected_toolsets)

    expected_toolsets_names = [t.name for t in expected_toolsets].sort()
    filtered_toolsets_names = [t.name for t in filtered_toolsets].sort()

    assert expected_toolsets_names == filtered_toolsets_names


@pytest.mark.parametrize(
    "toolset_name, expected_name",
    [
        ("datadog/traces", "Datadog"),
        ("datadog", "Datadog"),
        ("", ""),
    ],
)
def test_toolset_name_for_one_liner(toolset_name, expected_name):
    assert toolset_name_for_one_liner(toolset_name) == expected_name
