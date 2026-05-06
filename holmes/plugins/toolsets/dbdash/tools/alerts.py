from typing import TYPE_CHECKING, Dict

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

if TYPE_CHECKING:
    from holmes.plugins.toolsets.dbdash.dbdash_toolset import DBADashToolset


class GetActiveAlerts(Tool):
    """Get active alerts from DBADash."""

    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_active_alerts",
            description=(
                "Get active alerts from DBADash including priority, alert type, "
                "affected instance, message, and trigger date. Also returns alert "
                "counts by severity (critical, warning, info)."
            ),
            parameters={},
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset.client.get("/api/alerts", params={"status": "active"})
            alerts = data.get("alerts", [])

            if not alerts:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    error="No active alerts found.",
                    params=params,
                )

            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch active alerts from {self._toolset.dbdash_config.api_url}/api/alerts: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Active Alerts"


class GetClosedAlerts(Tool):
    """Get recently closed alerts from DBADash."""

    def __init__(self, toolset: "DBADashToolset"):
        super().__init__(
            name="dbdash_get_closed_alerts",
            description=(
                "Get recently closed alerts from DBADash for historical context. "
                "Useful for understanding patterns and recent resolutions."
            ),
            parameters={},
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset.client.get("/api/alerts", params={"status": "closed"})
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to fetch closed alerts from {self._toolset.dbdash_config.api_url}/api/alerts: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Closed Alerts"
