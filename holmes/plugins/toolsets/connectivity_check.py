import socket
from typing import Any, Dict, Literal

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner

BROWSER_LIKE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

UserAgentMode = Literal["none", "browser"]


def tcp_check(host: str, port: int, timeout: float) -> Dict[str, Any]:
    if not (1 <= port <= 65535):
        return {
            "ok": False,
            "error": "invalid port (must be 1-65535)",
        }

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "ok": True,
            }
    except (OSError, socket.timeout) as e:
        return {
            "ok": False,
            "error": str(e),
        }


class TcpCheckTool(Tool):
    toolset: "ConnectivityCheckToolset" = None  # type: ignore

    def __init__(self, toolset: "ConnectivityCheckToolset"):
        super().__init__(
            name="tcp_check",
            description="Check if a TCP socket can be opened to a host and port.",
            parameters={
                "host": ToolParameter(
                    description="The hostname or IP address to connect to",
                    type="string",
                    required=True,
                ),
                "port": ToolParameter(
                    description="The port to connect to",
                    type="integer",
                    required=True,
                ),
                "timeout": ToolParameter(
                    description="Timeout in seconds (default: 3.0)",
                    type="number",
                    required=False,
                ),
            },
        )
        self.toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        host = params.get("host")
        port = params.get("port")
        if host is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data={"error": "host parameter is required"},
                params=params,
            )
        if port is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data={"error": "port parameter is required"},
                params=params,
            )

        result = tcp_check(
            host=host,
            port=int(port),
            timeout=float(params.get("timeout", 3.0)),
        )
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=result,
            params=params,
        )

    def get_parameterized_one_liner(self, params) -> str:
        host = params.get("host", "<missing host>")
        port = params.get("port", "<missing port>")
        return (
            f"{toolset_name_for_one_liner(self.toolset.name)}: "
            f"TCP check {host}:{port}"
        )


class ConnectivityCheckToolset(Toolset):
    def __init__(self):
        super().__init__(
            name="connectivity_check",
            description="Check TCP connectivity to endpoints",
            icon_url="https://platform.robusta.dev/demos/internet-access.svg",
            tools=[
                TcpCheckTool(self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
            is_default=True,
            enabled=True,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/connectivity-check/",
        )

    def get_example_config(self) -> Dict[str, Any]:
        return {}
