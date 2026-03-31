from unittest.mock import MagicMock, patch

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
    toolset.dbdash_config = config
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


class TestGetSlowQueries:
    def test_returns_slow_queries(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetSlowQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "summary": [{"Grp": "Total", "Total": 50, "TotalDurationMs": 120000}],
            "detail": [{"InstanceDisplayName": "prod-sql-01", "SQLText": "SELECT *", "Duration": 5000}],
        }

        tool = GetSlowQueries(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS


class TestGetRunningQueries:
    def test_returns_running_queries(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetRunningQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"SPID": 55, "DatabaseName": "PaymentDB", "Duration": 120, "SQLText": "UPDATE orders..."}],
        }

        tool = GetRunningQueries(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS

    def test_blocked_only_filter(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetRunningQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {"data": []}

        tool = GetRunningQueries(toolset)
        result = tool._invoke({"instanceId": "1", "blockedOnly": "true"}, make_context())

        call_params = toolset.client.get.call_args[1]["params"]
        assert call_params["blockedOnly"] == "true"


class TestGetBlockingQueries:
    def test_returns_blocking_data(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetBlockingQueries

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "data": [{"HeadBlockerSPID": 55, "BlockedSPID": 60, "WaitType": "LCK_M_X"}],
            "summary": [{"WaitType": "LCK_M_X", "BlockingCount": 5}],
            "snapshots": [],
        }

        tool = GetBlockingQueries(toolset)
        result = tool._invoke({"instanceId": "1"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS


class TestGetQueryStoreTop:
    def test_returns_top_queries(self):
        from holmes.plugins.toolsets.dbdash.tools.queries import GetQueryStoreTop

        toolset = make_mock_toolset()
        toolset.client.get.return_value = {
            "databases": [{"DatabaseID": 1, "name": "PaymentDB"}],
            "data": [{"QueryID": 42, "QueryText": "SELECT * FROM orders", "TotalCPU": 50000}],
            "metric": "cpu",
        }

        tool = GetQueryStoreTop(toolset)
        result = tool._invoke({"instanceId": "1", "metric": "cpu"}, make_context())

        assert result.status == StructuredToolResultStatus.SUCCESS
        call_params = toolset.client.get.call_args[1]["params"]
        assert call_params["metric"] == "cpu"


class TestDBADashToolsetIntegration:
    """Smoke test: verify the toolset initializes and all tools are wired correctly."""

    def test_toolset_has_all_12_tools(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset()
        assert len(toolset.tools) == 12

        tool_names = {t.name for t in toolset.tools}
        expected_names = {
            "dbdash_list_instances",
            "dbdash_get_instance_details",
            "dbdash_get_active_alerts",
            "dbdash_get_closed_alerts",
            "dbdash_get_cpu_metrics",
            "dbdash_get_memory_metrics",
            "dbdash_get_wait_stats",
            "dbdash_get_io_stats",
            "dbdash_get_slow_queries",
            "dbdash_get_running_queries",
            "dbdash_get_blocking_queries",
            "dbdash_get_query_store_top",
        }
        assert tool_names == expected_names

    def test_toolset_name_and_description(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset()
        assert toolset.name == "dbdash"
        assert "SQL Server" in toolset.description

    def test_toolset_custom_name(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset(name="dbdash:payments")
        assert toolset.name == "dbdash:payments"

    def test_prerequisites_fail_without_config(self):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        toolset = DBADashToolset()
        success, error = toolset.prerequisites_callable({})
        assert success is False
        assert "missing" in error.lower()

    @patch("holmes.plugins.toolsets.dbdash.common.Cognito")
    @patch("holmes.plugins.toolsets.dbdash.common.requests.Session")
    def test_prerequisites_succeed_with_valid_config(self, mock_session_cls, mock_cognito_cls):
        from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset

        mock_cognito = MagicMock()
        mock_cognito.id_token = "fake-id-token"
        mock_cognito.refresh_token = "fake-refresh-token"
        mock_cognito_cls.return_value = mock_cognito

        mock_session = MagicMock()
        login_response = MagicMock()
        login_response.status_code = 200
        login_response.json.return_value = {"success": True, "user": {"username": "holmes"}}
        health_response = MagicMock()
        health_response.status_code = 200
        health_response.json.return_value = {"status": "connected"}
        health_response.raise_for_status = MagicMock()
        mock_session.post.return_value = login_response
        mock_session.get.return_value = health_response
        mock_session_cls.return_value = mock_session

        toolset = DBADashToolset()
        success, error = toolset.prerequisites_callable({
            "api_url": "https://db-monitor.example.com",
            "username": "holmes",
            "password": "secret123",
            "cognito_user_pool_id": "us-east-1_TEST",
            "cognito_client_id": "test-client",
        })

        assert success is True
        assert error == ""
