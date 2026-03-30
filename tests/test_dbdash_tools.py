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
