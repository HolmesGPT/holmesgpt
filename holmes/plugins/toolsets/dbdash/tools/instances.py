from typing import TYPE_CHECKING, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.dbdash.common import filter_instances_by_tags
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset


class ListInstances(Tool):
    """List SQL Server instances, filtered by configured tags."""

    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_list_instances",
            description=(
                "List SQL Server database instances monitored by DBADash (also known as 'db dash'). "
                "Use this tool when the user asks about SQL Server databases, DB instances, "
                "DBADash, db dash, or database servers. Returns instance IDs and display names. "
                "If tags are configured, only instances matching those tags are returned."
            ),
            parameters={},
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            instances = self._toolset.client.get("/api/instances")

            configured_tags = self._toolset.dbdash_config.instance_tags
            if configured_tags:
                tags_response = self._toolset.client.get("/api/settings/tags")
                instance_tags = tags_response.get("instanceTags", [])
                instances = filter_instances_by_tags(instances, instance_tags, configured_tags)

            if not instances:
                tag_desc = f" matching tags {configured_tags}" if configured_tags else ""
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error=f"No SQL Server instances found{tag_desc}.",
                    params=params,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=instances,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to list instances from {self._toolset.dbdash_config.api_url}/api/instances: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List Instances"


class GetInstanceDetails(Tool):
    """Get detailed information about a specific SQL Server instance."""

    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_instance_details",
            description=(
                "Get detailed information about a SQL Server instance including "
                "hardware specs, configuration, and collection dates."
            ),
            parameters={
                "instanceId": ToolParameter(
                    description="The instance ID (from dbdash_list_instances)",
                    type="string",
                    required=True,
                ),
            },
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        instance_id = params.get("instanceId")
        if not instance_id:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter: instanceId",
                params=params,
            )

        try:
            data = self._toolset.client.get(f"/api/instances/{instance_id}")
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Failed to get instance details from "
                    f"{self._toolset.dbdash_config.api_url}/api/instances/{instance_id}: {e}"
                ),
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Instance {params.get('instanceId', '')}"
