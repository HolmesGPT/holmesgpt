import http.client
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


def tcp_check(host: str, port: int, timeout: float = 3.0) -> Dict[str, Any]:
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "invalid port (must be 1-65535)",
        }

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


def http_check(
    host: str,
    port: int,
    path: str = "/",
    timeout: float = 3.0,
    https: bool = False,
    user_agent: UserAgentMode = "none",
) -> Dict[str, Any]:
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid port"}

    if not (1 <= port <= 65535):
        return {"ok": False, "error": "invalid port"}

    conn_class = http.client.HTTPSConnection if https else http.client.HTTPConnection

    try:
        conn = conn_class(host, port, timeout=timeout)
        headers = {
            "Host": host,
        }

        if user_agent == "browser":
            headers["User-Agent"] = BROWSER_LIKE_UA

        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        return {
            "ok": 200 <= resp.status < 400,
            "status": resp.status,
            "reason": resp.reason,
        }
    except (OSError, socket.timeout) as e:
        return {"ok": False, "error": str(e)}
    finally:
        if "conn" in locals():
            conn.close()


class HttpCheckTool(Tool):
    toolset: "ConnectivityCheckToolset" = None  # type: ignore

    def __init__(self, toolset: "ConnectivityCheckToolset"):
        super().__init__(
            name="http_check",
            description=(
                "Check if an HTTP or HTTPS endpoint is reachable and return the status"
                " code."
            ),
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
                "path": ToolParameter(
                    description="Path to request (default: /)",
                    type="string",
                    required=False,
                ),
                "timeout": ToolParameter(
                    description="Timeout in seconds (default: 3.0)",
                    type="number",
                    required=False,
                ),
                "https": ToolParameter(
                    description="Use HTTPS when true (default: false)",
                    type="boolean",
                    required=False,
                ),
                "user_agent": ToolParameter(
                    description="User-Agent mode: none or browser",
                    type="string",
                    enum=["none", "browser"],
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

        user_agent_value = params.get("user_agent", "none")
        if user_agent_value not in ["none", "browser"]:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data={
                    "error": f"Invalid user_agent '{user_agent_value}'. Must be 'none' or 'browser'"
                },
                params=params,
            )

        result = http_check(
            host=host,
            port=int(port),
            path=params.get("path", "/"),
            timeout=float(params.get("timeout", 3.0)),
            https=bool(params.get("https", False)),
            user_agent=user_agent_value,
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
            f"HTTP check {host}:{port}"
        )


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
            description="Check HTTP and TCP connectivity to endpoints",
            icon_url="https://platform.robusta.dev/demos/internet-access.svg",
            tools=[
                HttpCheckTool(self),
                TcpCheckTool(self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
            is_default=True,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/connectivity-check/",
        )

    def get_example_config(self) -> Dict[str, Any]:
        return {}
