import base64
import logging
import os
from abc import ABC
from typing import Any, ClassVar, Dict, Optional, Tuple, Type, cast
from urllib.parse import urlencode, urljoin

import requests  # type: ignore
from pydantic import Field

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.grafana.base_grafana_toolset import BaseGrafanaToolset
from holmes.plugins.toolsets.grafana.common import (
    GrafanaConfig,
    build_headers,
    get_base_url,
)
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

logger = logging.getLogger(__name__)


class GrafanaDashboardConfig(GrafanaConfig):
    """Configuration specific to Grafana Dashboard toolset."""

    enable_rendering: bool = Field(
        default=False,
        title="Enable Rendering",
        description="Enable panel/dashboard image rendering via Grafana Image Renderer. "
        "Requires the grafana-image-renderer plugin to be installed on the Grafana instance.",
    )
    default_render_width: int = Field(
        default=800,
        title="Default Render Width",
        description="Default width in pixels for rendered panel/dashboard images",
    )
    default_render_height: int = Field(
        default=400,
        title="Default Render Height",
        description="Default height in pixels for rendered panel images",
    )


def _build_grafana_dashboard_url(
    config: GrafanaDashboardConfig,
    uid: Optional[str] = None,
    query_params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    try:
        base_url = config.external_url or config.api_url
        if uid:
            return f"{base_url.rstrip('/')}/d/{uid}"
        else:
            query_string = urlencode(query_params, doseq=True) if query_params else ""
            if query_string:
                return f"{base_url.rstrip('/')}/dashboards?{query_string}"
            else:
                return f"{base_url.rstrip('/')}/dashboards"
    except Exception:
        return None


class GrafanaToolset(BaseGrafanaToolset):
    config_classes: ClassVar[list[Type[GrafanaDashboardConfig]]] = [GrafanaDashboardConfig]

    def __init__(self):
        super().__init__(
            name="grafana/dashboards",
            description="Provides tools for interacting with Grafana dashboards, including visual rendering of panels and dashboards",
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

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            logger.debug(f"Grafana config not provided {self.name}")
            from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR

            return False, TOOLSET_CONFIG_MISSING_ERROR

        try:
            self._grafana_config = GrafanaDashboardConfig(**config)
            healthy, err = self.health_check()
            if not healthy:
                return False, err

            # After base health check passes, conditionally add render tools
            if self.grafana_config.enable_rendering:
                self._try_add_render_tools()

            return True, ""
        except Exception as e:
            logger.exception(f"Failed to set up grafana toolset {self.name}")
            return False, str(e)

    def _try_add_render_tools(self) -> None:
        """Check if Grafana Image Renderer is available and add render tools."""
        try:
            config = self.grafana_config
            base_url = config.api_url.rstrip("/")
            headers = build_headers(
                api_key=config.api_key,
                additional_headers=config.additional_headers,
            )
            renderer_detected = False

            # Try the rendering version API first
            try:
                response = requests.get(
                    f"{base_url}/api/rendering/version",
                    headers=headers,
                    timeout=10,
                    verify=config.verify_ssl,
                )
                if response.status_code == 200:
                    version_info = response.json()
                    logger.info(
                        f"Grafana Image Renderer available (version: {version_info}). "
                        f"Enabling render tools."
                    )
                    renderer_detected = True
            except Exception as e:
                logger.debug(f"Failed to check renderer version API: {e}")

            # Fallback: check if GF_RENDERING_SERVER_URL is configured by attempting
            # a small render request. Some Grafana versions don't expose the version API
            # but still support rendering.
            if not renderer_detected:
                try:
                    response = requests.get(
                        f"{base_url}/render/d-solo/nonexistent/_?panelId=1&width=100&height=100",
                        headers=headers,
                        timeout=15,
                        verify=config.verify_ssl,
                    )
                    # If renderer is configured, we get a 200 (rendered image) or
                    # 500 (dashboard not found but renderer is present).
                    # 404 means rendering is not available. 401/403 means auth issues,
                    # not renderer presence.
                    if response.status_code in (200, 500):
                        logger.info(
                            f"Grafana Image Renderer detected via render endpoint "
                            f"(HTTP {response.status_code}). Enabling render tools."
                        )
                        renderer_detected = True
                except Exception as e:
                    logger.debug(f"Failed to check renderer via render endpoint: {e}")

            if renderer_detected:
                self.tools.append(RenderPanel(self))
                self.tools.append(RenderDashboard(self))
            else:
                logger.info(
                    "Grafana Image Renderer not available. "
                    "Render tools will not be registered. "
                    "Install grafana-image-renderer plugin to enable visual dashboard analysis."
                )
        except Exception as e:
            logger.info(
                f"Could not check Grafana Image Renderer availability: {e}. "
                f"Render tools will not be registered."
            )

    def health_check(self) -> Tuple[bool, str]:
        """Test connectivity by invoking GetDashboardTags tool."""
        tool = GetDashboardTags(self)
        try:
            _ = tool._make_grafana_request("api/dashboards/tags", {})
            return True, ""
        except Exception as e:
            return False, f"Failed to connect to Grafana {str(e)}"

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
        base_url = get_base_url(self._toolset.grafana_config)
        if not base_url.endswith("/"):
            base_url += "/"
        url = urljoin(base_url, endpoint)
        headers = build_headers(
            api_key=self._toolset.grafana_config.api_key,
            additional_headers=self._toolset.grafana_config.additional_headers,
        )

        response = requests.get(
            url,
            headers=headers,
            params=query_params,
            timeout=timeout,
            verify=self._toolset.grafana_config.verify_ssl,
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

        result = self._make_grafana_request("api/search", params, query_params)

        config = self._toolset.grafana_config
        search_url = _build_grafana_dashboard_url(config, query_params=query_params)

        if params.get("dashboardUIDs"):
            uids = [
                uid.strip() for uid in params["dashboardUIDs"].split(",") if uid.strip()
            ]
            if len(uids) == 1:
                search_url = _build_grafana_dashboard_url(config, uid=uids[0])

        return StructuredToolResult(
            status=result.status,
            data=result.data,
            params=result.params,
            url=search_url if search_url else None,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search Dashboards"


class GetDashboardByUID(JsonFilterMixin, BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_dashboard_by_uid",
            description="Get a dashboard by its UID using the /api/dashboards/uid/:uid endpoint",
            parameters=self.extend_parameters(
                {
                    "uid": ToolParameter(
                        description="The unique identifier of the dashboard",
                        type="string",
                        required=True,
                    )
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        uid = params["uid"]
        result = self._make_grafana_request(f"api/dashboards/uid/{uid}", params)

        dashboard_url = _build_grafana_dashboard_url(
            self._toolset.grafana_config, uid=uid
        )

        filtered_result = self.filter_result(result, params)
        filtered_result.url = dashboard_url if dashboard_url else result.url
        return filtered_result

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Dashboard {params.get('uid', '')}"


class GetHomeDashboard(JsonFilterMixin, BaseGrafanaTool):
    def __init__(self, toolset: GrafanaToolset):
        super().__init__(
            toolset=toolset,
            name="grafana_get_home_dashboard",
            description="Get the home dashboard using the /api/dashboards/home endpoint",
            parameters=self.extend_parameters({}),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        result = self._make_grafana_request("api/dashboards/home", params)
        config = self._toolset.grafana_config
        dashboard_url = None
        if isinstance(result.data, dict):
            uid = result.data.get("dashboard", {}).get("uid")
            if uid:
                dashboard_url = _build_grafana_dashboard_url(config, uid=uid)

        filtered_result = self.filter_result(result, params)
        filtered_result.url = dashboard_url if dashboard_url else None
        return filtered_result

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
        result = self._make_grafana_request("api/dashboards/tags", params)

        config = self._toolset.grafana_config
        tags_url = _build_grafana_dashboard_url(config)

        return StructuredToolResult(
            status=result.status,
            data=result.data,
            params=result.params,
            url=tags_url,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get Dashboard Tags"


# --- Render tools for Grafana dashboard/panel screenshots via vision ---

RENDER_COMMON_PARAMS: Dict[str, ToolParameter] = {
    "from_time": ToolParameter(
        description="Start time for the render. Accepts Grafana time formats: "
        "relative (e.g. 'now-6h', 'now-1d', 'now-30m') or "
        "epoch milliseconds (e.g. '1609459200000'). Default: 'now-6h'",
        type="string",
        required=False,
    ),
    "to_time": ToolParameter(
        description="End time for the render. Accepts Grafana time formats: "
        "relative (e.g. 'now', 'now-1h') or "
        "epoch milliseconds (e.g. '1609459200000'). Default: 'now'",
        type="string",
        required=False,
    ),
    "width": ToolParameter(
        description="Image width in pixels. Default is set by toolset config.",
        type="integer",
        required=False,
    ),
    "height": ToolParameter(
        description="Image height in pixels. Default is set by toolset config.",
        type="integer",
        required=False,
    ),
    "theme": ToolParameter(
        description="Dashboard theme: 'light' or 'dark'. Default: 'dark'",
        type="string",
        required=False,
    ),
    "timezone": ToolParameter(
        description="Timezone for the render (e.g. 'UTC', 'America/New_York', 'browser'). Default: '' (Grafana default)",
        type="string",
        required=False,
    ),
    "variables": ToolParameter(
        description="Template variables as semicolon-separated key=value pairs. "
        "Example: 'var-namespace=production;var-cluster=us-east-1'. "
        "Each variable must be prefixed with 'var-'.",
        type="string",
        required=False,
    ),
}


def _build_render_query_params(
    params: dict,
    default_width: int,
    default_height: int,
) -> Dict[str, Any]:
    """Build query parameters for Grafana render API from tool params."""
    query_params: Dict[str, Any] = {
        "from": params.get("from_time", "now-6h"),
        "to": params.get("to_time", "now"),
        "width": params.get("width", default_width),
        "height": params.get("height", default_height),
        "theme": params.get("theme", "dark"),
    }

    timezone = params.get("timezone")
    if timezone:
        query_params["tz"] = timezone

    # Parse template variables: "var-namespace=prod;var-cluster=east"
    variables_str = params.get("variables", "")
    if variables_str:
        for var_pair in variables_str.split(";"):
            var_pair = var_pair.strip()
            if "=" in var_pair:
                key, value = var_pair.split("=", 1)
                query_params[key.strip()] = value.strip()

    return query_params


class BaseGrafanaRenderTool(Tool, ABC):
    """Base class for Grafana render tools that return panel/dashboard screenshots."""

    def __init__(self, toolset: "GrafanaToolset", *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _make_render_request(
        self,
        render_path: str,
        query_params: Dict[str, Any],
        timeout: int = 60,
    ) -> bytes:
        """Make a GET request to Grafana render API and return PNG bytes.

        Args:
            render_path: Render URL path (e.g. "render/d-solo/uid/slug")
            query_params: Query parameters for the render request
            timeout: Request timeout in seconds (rendering can be slow)

        Returns:
            PNG image bytes

        Raises:
            requests.HTTPError: If the request fails
        """
        config = self._toolset.grafana_config
        base_url = get_base_url(config)
        if not base_url.endswith("/"):
            base_url += "/"
        url = urljoin(base_url, render_path)
        headers = build_headers(
            api_key=config.api_key,
            additional_headers=config.additional_headers,
        )
        # Render API returns PNG, not JSON
        headers["Accept"] = "image/png"

        response = requests.get(
            url,
            headers=headers,
            params=query_params,
            timeout=timeout,
            verify=config.verify_ssl,
        )
        response.raise_for_status()
        return response.content

    def _render_to_result(
        self,
        render_path: str,
        params: dict,
        query_params: Dict[str, Any],
        description: str,
        dashboard_url: Optional[str] = None,
    ) -> StructuredToolResult:
        """Render a panel/dashboard and return a StructuredToolResult with the image."""
        try:
            png_bytes = self._make_render_request(render_path, query_params)
        except requests.HTTPError as e:
            status_code = (
                e.response.status_code if e.response is not None else "unknown"
            )
            query_string = urlencode(query_params, doseq=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Grafana render API returned HTTP {status_code}: {e}. "
                f"Render path: {render_path}?{query_string}. "
                f"Ensure the grafana-image-renderer plugin is installed and running.",
                params=params,
            )
        except requests.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to connect to Grafana render API at {render_path}: {e}",
                params=params,
            )
        except requests.Timeout:
            query_string = urlencode(query_params, doseq=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Grafana render request timed out for {render_path}?{query_string}. "
                f"The panel may be too complex or the renderer is overloaded.",
                params=params,
            )

        b64_data = base64.b64encode(png_bytes).decode("utf-8")
        image_url = f"data:image/png;base64,{b64_data}"

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=description,
            images=[{"url": image_url, "detail": "auto"}],
            params=params,
            url=dashboard_url,
        )


class RenderPanel(BaseGrafanaRenderTool):
    def __init__(self, toolset: "GrafanaToolset"):
        panel_params: Dict[str, ToolParameter] = {
            "dashboard_uid": ToolParameter(
                description="The UID of the dashboard containing the panel",
                type="string",
                required=True,
            ),
            "panel_id": ToolParameter(
                description="The numeric ID of the panel to render (found in dashboard JSON under panels[].id)",
                type="integer",
                required=True,
            ),
        }
        panel_params.update(RENDER_COMMON_PARAMS)
        super().__init__(
            toolset=toolset,
            name="grafana_render_panel",
            description="Render a single Grafana dashboard panel as a PNG screenshot using the Grafana Image Renderer. "
            "Returns the image for visual analysis. Use this to visually inspect graphs, charts, and gauges. "
            "Requires the grafana-image-renderer plugin on the Grafana instance.",
            parameters=panel_params,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        config = self._toolset.grafana_config
        dashboard_uid = params["dashboard_uid"]
        panel_id = params["panel_id"]

        query_params = _build_render_query_params(
            params,
            default_width=config.default_render_width,
            default_height=config.default_render_height,
        )
        query_params["panelId"] = panel_id

        render_path = f"render/d-solo/{dashboard_uid}/_"
        dashboard_url = _build_grafana_dashboard_url(config, uid=dashboard_uid)

        description = (
            f"Rendered screenshot of panel {panel_id} from dashboard {dashboard_uid}. "
            f"Time range: {query_params['from']} to {query_params['to']}, "
            f"size: {query_params['width']}x{query_params['height']}px."
        )

        return self._render_to_result(
            render_path=render_path,
            params=params,
            query_params=query_params,
            description=description,
            dashboard_url=dashboard_url,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: "
            f"Render Panel {params.get('panel_id', '?')} from Dashboard {params.get('dashboard_uid', '?')}"
        )


class RenderDashboard(BaseGrafanaRenderTool):
    def __init__(self, toolset: "GrafanaToolset"):
        dashboard_params: Dict[str, ToolParameter] = {
            "dashboard_uid": ToolParameter(
                description="The UID of the dashboard to render",
                type="string",
                required=True,
            ),
        }
        dashboard_params.update(RENDER_COMMON_PARAMS)
        super().__init__(
            toolset=toolset,
            name="grafana_render_dashboard",
            description="Render an entire Grafana dashboard as a PNG screenshot using the Grafana Image Renderer. "
            "Returns the full dashboard image for visual overview. Use this to get a bird's-eye view of all panels. "
            "For detailed inspection of individual panels, use grafana_render_panel instead. "
            "Requires the grafana-image-renderer plugin on the Grafana instance.",
            parameters=dashboard_params,
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        config = self._toolset.grafana_config
        dashboard_uid = params["dashboard_uid"]

        query_params = _build_render_query_params(
            params,
            default_width=config.default_render_width,
            default_height=config.default_render_height,
        )
        # Use full-page rendering (height=-1) to capture the entire dashboard
        # regardless of how many panels/rows it has. The Grafana Image Renderer
        # scrolls the full page and stitches the result into one image.
        if "height" not in params:
            query_params["height"] = -1

        render_path = f"render/d/{dashboard_uid}/_"
        dashboard_url = _build_grafana_dashboard_url(config, uid=dashboard_uid)

        height_desc = "full-page" if query_params["height"] == -1 else f"{query_params['height']}px"
        description = (
            f"Rendered screenshot of full dashboard {dashboard_uid}. "
            f"Time range: {query_params['from']} to {query_params['to']}, "
            f"width: {query_params['width']}px, height: {height_desc}."
        )

        return self._render_to_result(
            render_path=render_path,
            params=params,
            query_params=query_params,
            description=description,
            dashboard_url=dashboard_url,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: "
            f"Render Dashboard {params.get('dashboard_uid', '?')}"
        )
