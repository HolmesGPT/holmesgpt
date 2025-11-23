import os
from typing import ClassVar, Dict, Optional, Type, cast
from urllib.parse import urljoin
from abc import ABC
from holmes.core.tools import (
    StructuredToolResult,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    StructuredToolResultStatus,
)
from holmes.plugins.toolsets.grafana.base_grafana_toolset import BaseGrafanaToolset
import requests  # type: ignore

from holmes.plugins.toolsets.grafana.common import (
    get_base_url,
    GrafanaConfig,
    build_headers,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class GrafanaDashboardConfig(GrafanaConfig):
    """Configuration specific to Grafana Dashboard toolset with api/health as default healthcheck"""

    healthcheck: Optional[str] = "api/health"


class GrafanaToolset(BaseGrafanaToolset):
    config_class: ClassVar[Type[GrafanaDashboardConfig]] = GrafanaDashboardConfig

    def __init__(self):
        super().__init__(
            name="grafana/dashboards",
            description="Provides tools for interacting with Grafana dashboards",
            icon_url="https://w7.pngwing.com/pngs/434/923/png-transparent-grafana-hd-logo-thumbnail.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/grafanadashboards/",
            tools=[
                SearchDashboards(self),
                GetDashboardByUID(self),
                GetHomeDashboard(self),
                GetDashboardTags(self),
            ],
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "toolset_grafana_dashboard.jinja2"
        )

    @property
    def grafana_config(self) -> GrafanaDashboardConfig:
        return cast(GrafanaDashboardConfig, self._grafana_config)


class BaseGrafanaTool(Tool, ABC):
    """Base class for Grafana tools with common HTTP request functionality."""

    def __init__(self, toolset: GrafanaToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _make_grafana_request(
        self,
        endpoint: str,
        params: dict,
        query_params: Optional[Dict] = None,
        timeout: int = 30,
    ) -> StructuredToolResult:
        """Make a GET request to Grafana API and return structured result.

        Args:
            endpoint: API endpoint path (e.g., "/api/search")
            params: Original parameters passed to the tool
            query_params: Optional query parameters for the request

        Returns:
            StructuredToolResult with the API response data
        """
        url = urljoin(get_base_url(self._toolset.grafana_config), endpoint)
        headers = build_headers(
            api_key=self._toolset.grafana_config.api_key,
            additional_headers=self._toolset.grafana_config.headers,
        )

        response = requests.get(
            url, headers=headers, params=query_params, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            url=url,
            params=params,
        )


class SearchDashboards(BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_search_dashboards",
            description="Search for Grafana dashboards and folders using the /api/search endpoint",
            parameters={
                "query": ToolParameter(
                    description="Search text to filter dashboards",
                    type="string",
                    required=False,
                ),
                "tag": ToolParameter(
                    description="Search dashboards by tag",
                    type="string",
                    required=False,
                ),
                "type": ToolParameter(
                    description="Filter by type: 'dash-folder' or 'dash-db'",
                    type="string",
                    required=False,
                ),
                "dashboardIds": ToolParameter(
                    description="List of dashboard IDs to filter (comma-separated)",
                    type="string",
                    required=False,
                ),
                "dashboardUIDs": ToolParameter(
                    description="List of dashboard UIDs to search for (comma-separated)",
                    type="string",
                    required=False,
                ),
                "folderUIDs": ToolParameter(
                    description="List of folder UIDs to search within (comma-separated)",
                    type="string",
                    required=False,
                ),
                "starred": ToolParameter(
                    description="Return only starred dashboards",
                    type="boolean",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum results (default 1000, max 5000)",
                    type="integer",
                    required=False,
                ),
                "page": ToolParameter(
                    description="Page number for pagination",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        query_params = {}
        if params.get("query"):
            query_params["query"] = params["query"]
        if params.get("tag"):
            query_params["tag"] = params["tag"]
        if params.get("type"):
            query_params["type"] = params["type"]
        if params.get("dashboardIds"):
            # Check if dashboardIds also needs to be passed as multiple params
            dashboard_ids = params["dashboardIds"].split(",")
            query_params["dashboardIds"] = [
                dashboard_id.strip()
                for dashboard_id in dashboard_ids
                if dashboard_id.strip()
            ]
        if params.get("dashboardUIDs"):
            # Handle dashboardUIDs as a list - split comma-separated values
            dashboard_uids = params["dashboardUIDs"].split(",")
            query_params["dashboardUIDs"] = [
                uid.strip() for uid in dashboard_uids if uid.strip()
            ]
        if params.get("folderUIDs"):
            # Check if folderUIDs also needs to be passed as multiple params
            folder_uids = params["folderUIDs"].split(",")
            query_params["folderUIDs"] = [
                uid.strip() for uid in folder_uids if uid.strip()
            ]
        if params.get("starred") is not None:
            query_params["starred"] = str(params["starred"]).lower()
        if params.get("limit"):
            query_params["limit"] = params["limit"]
        if params.get("page"):
            query_params["page"] = params["page"]

        return self._make_grafana_request("/api/search", params, query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search Dashboards"


class GetDashboardByUID(BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_dashboard_by_uid",
            description="Get a dashboard by its UID using the /api/dashboards/uid/:uid endpoint",
            parameters={
                "uid": ToolParameter(
                    description="The unique identifier of the dashboard",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        uid = params["uid"]
        return self._make_grafana_request(f"/api/dashboards/uid/{uid}", params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Dashboard {params.get('uid', '')}"


class GetHomeDashboard(BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_home_dashboard",
            description="Get the home dashboard using the /api/dashboards/home endpoint",
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._make_grafana_request("/api/dashboards/home", params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Home Dashboard"


class GetDashboardTags(BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_dashboard_tags",
            description="Get all tags used across dashboards using the /api/dashboards/tags endpoint",
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._make_grafana_request("/api/dashboards/tags", params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Dashboard Tags"
