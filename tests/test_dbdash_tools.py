from unittest.mock import MagicMock

import pytest

from holmes.core.tools import StructuredToolResultStatus
from holmes.plugins.toolsets.dbdash.common import DBADashClient, DBADashConfig
from tests.conftest import create_mock_tool_invoke_context


def make_mock_toolset(instance_tags=None):
    """Create a mock toolset with a mock client."""
    config = DBADashConfig(
        api_url="https://db-monitor.example.com",
        username="holmes",
        password="secret123",
        instance_tags=instance_tags,
    )
    toolset = MagicMock()
    toolset.config = config
    toolset.client = MagicMock(spec=DBADashClient)
    toolset.name = "dbdash"
    return toolset


def make_context():
    return create_mock_tool_invoke_context()


class TestListInstances:
    def test_returns_all_instances_when_no_tags(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import ListInstances

        toolset = make_mock_toolset(instance_tags=None)
        toolset.client.get.side_effect = [
            [
                {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
                {"InstanceID": 2, "InstanceDisplayName": "prod-sql-02"},
            ],
        ]

        tool = ListInstances(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert len(result.data) == 2

    def test_filters_by_tags_when_configured(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import ListInstances

        toolset = make_mock_toolset(instance_tags={"project": "payments"})
        toolset.client.get.side_effect = [
            [
                {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
                {"InstanceID": 2, "InstanceDisplayName": "staging-sql-01"},
            ],
            {
                "instanceTags": [
                    {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01", "TagName": "project", "TagValue": "payments"},
                    {"InstanceID": 2, "InstanceDisplayName": "staging-sql-01", "TagName": "project", "TagValue": "staging"},
                ],
            },
        ]

        tool = ListInstances(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert len(result.data) == 1
        assert result.data[0]["InstanceID"] == 1

    def test_returns_no_data_when_no_instances_match(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import ListInstances

        toolset = make_mock_toolset(instance_tags={"project": "nonexistent"})
        toolset.client.get.side_effect = [
            [{"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"}],
            {"instanceTags": [{"InstanceID": 1, "InstanceDisplayName": "prod-sql-01", "TagName": "project", "TagValue": "payments"}]},
        ]

        tool = ListInstances(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.NO_DATA


class TestGetInstanceDetails:
    def test_returns_instance_details(self):
        from holmes.plugins.toolsets.dbdash.tools.instances import GetInstanceDetails

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "instance": {"InstanceID": 1, "InstanceDisplayName": "prod-sql-01"},
            "hardware": [{"Name": "CPU", "Value": "8 cores"}],
            "collectionDates": [{"CollectionType": "CPU", "LastCollected": "2026-03-30T10:00:00Z"}],
        }

        tool = GetInstanceDetails(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        toolset.client.get.assert_called_once_with("/api/instances/1")


class TestGetActiveAlerts:
    def test_returns_active_alerts(self):
        from holmes.plugins.toolsets.dbdash.tools.alerts import GetActiveAlerts

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "alerts": [
                {
                    "Priority": 1,
                    "AlertType": "CPU",
                    "AlertKey": "prod-sql-01",
                    "InstanceDisplayName": "prod-sql-01",
                    "FirstMessage": "CPU > 90%",
                    "TriggerDate": "2026-03-30T10:00:00Z",
                    "UpdateCount": 3,
                    "IsAcknowledged": False,
                },
            ],
            "counts": {"critical": 1, "warning": 0, "info": 0, "acknowledged": 0},
        }

        tool = GetActiveAlerts(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert len(result.data["alerts"]) == 1
        toolset.client.get.assert_called_once_with("/api/alerts", params={"status": "active"})

    def test_returns_no_data_when_no_alerts(self):
        from holmes.plugins.toolsets.dbdash.tools.alerts import GetActiveAlerts

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "alerts": [],
            "counts": {"critical": 0, "warning": 0, "info": 0, "acknowledged": 0},
        }

        tool = GetActiveAlerts(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.NO_DATA


class TestGetClosedAlerts:
    def test_returns_closed_alerts(self):
        from holmes.plugins.toolsets.dbdash.tools.alerts import GetClosedAlerts

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "alerts": [
                {
                    "AlertType": "Memory",
                    "InstanceDisplayName": "prod-sql-01",
                    "ClosedDate": "2026-03-30T09:00:00Z",
                },
            ],
        }

        tool = GetClosedAlerts(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        toolset.client.get.assert_called_once_with("/api/alerts", params={"status": "closed"})


class TestGetCpuMetrics:
    def test_returns_cpu_data(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetCpuMetrics

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"EventTime": "2026-03-30T10:00:00Z", "SQLProcessCPU": 85, "OtherCPU": 5, "MaxCPU": 90}],
            "histogram": [{"CPUBucket": 90, "OccurrenceCount": 15}],
        }

        tool = GetCpuMetrics(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        toolset.client.get.assert_called_once()
        call_args = toolset.client.get.call_args
        assert call_args[0][0] == "/api/performance/cpu"
        assert call_args[1]["params"]["instanceId"] == "1"

    def test_returns_no_data_when_empty(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetCpuMetrics

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {"data": [], "histogram": []}

        tool = GetCpuMetrics(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.NO_DATA

    def test_missing_instance_id_returns_error(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetCpuMetrics

        toolset = make_mock_toolset()
        tool = GetCpuMetrics(toolset)
        result = tool._invoke({}, make_context())

        assert result.status == StructuredToolResultStatus.ERROR


class TestGetWaitStats:
    def test_returns_wait_data(self):
        from holmes.plugins.toolsets.dbdash.tools.performance import GetWaitStats

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"Time": "2026-03-30T10:00:00Z", "WaitType": "PAGEIOLATCH_SH", "TotalWaitSec": 120}],
            "summary": [{"WaitType": "PAGEIOLATCH_SH", "Description": "I/O wait", "TotalWaitSec": 120}],
        }

        tool = GetWaitStats(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
