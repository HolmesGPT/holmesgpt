import logging
import socket
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Type

from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.internet.ssrf import (
    SSRFValidationError,
    validate_host,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

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

        try:
            port_int = int(port)
        except (TypeError, ValueError):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data={"error": f"invalid port: {port!r}"},
                params=params,
            )

        # SSRF guard: the host is model-controlled, so refuse cloud-metadata /
        # loopback / link-local targets (a blind port-scan / recon primitive via
        # prompt injection). Private cluster IPs stay reachable by default since
        # probing internal services is this tool's legitimate purpose. Connect to
        # the validated IP so a DNS rebind cannot redirect the probe.
        config = self.toolset.effective_config
        try:
            validated_ips = validate_host(
                host,
                port_int,
                allowed_hosts=config.allowed_hosts,
                block_internal_ips=config.block_internal_ips,
                allow_private_ips=not config.block_private_ips,
            )
        except SSRFValidationError as e:
            error_message = f"Refusing to connect to {host}:{port_int}: {e}"
            logging.warning(error_message)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data={"ok": False, "error": error_message},
                params=params,
            )

        result = tcp_check(
            host=validated_ips[0],
            port=port_int,
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


class ConnectivityCheckConfig(ToolsetConfig):
    allowed_hosts: List[str] = Field(
        default_factory=list,
        title="Allowed hosts",
        description=(
            "Optional allowlist of hosts the check may target. When set, only "
            "these hosts (and their subdomains) may be probed, and they are "
            "exempt from the internal-IP block so an operator can deliberately "
            "target a specific endpoint."
        ),
    )
    block_internal_ips: bool = Field(
        default=True,
        title="Block internal IPs",
        description=(
            "Block cloud-metadata (169.254.0.0/16), loopback, link-local, "
            "multicast, reserved and unspecified targets (SSRF protection). "
            "Private/cluster IPs remain reachable unless block_private_ips is "
            "set. Only disable in trusted, isolated environments."
        ),
    )
    block_private_ips: bool = Field(
        default=False,
        title="Block private IPs",
        description=(
            "Also block private/RFC1918 ranges. Off by default because checking "
            "connectivity to internal cluster services is the tool's main "
            "purpose; enable to restrict the tool to public hosts only."
        ),
    )


class ConnectivityCheckToolset(Toolset):
    config_classes: ClassVar[list[Type[ConnectivityCheckConfig]]] = [
        ConnectivityCheckConfig
    ]

    connectivity_config: Optional[ConnectivityCheckConfig] = None

    def __init__(self):
        super().__init__(
            name="connectivity_check",
            description="Check TCP connectivity to endpoints",
            icon_url="https://platform.robusta.dev/demos/internet-access.svg",
            prerequisites=[
                CallablePrerequisite(callable=self.prerequisites_callable),
            ],
            tools=[
                TcpCheckTool(self),
            ],
            tags=[
                ToolsetTag.CORE,
            ],
            enabled=True,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/connectivity-check/",
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.connectivity_config = ConnectivityCheckConfig(**(config or {}))
        except Exception as e:
            return False, f"Invalid {self.name} configuration: {e}"
        return True, ""

    @property
    def effective_config(self) -> ConnectivityCheckConfig:
        """Config with safe defaults even if prerequisites haven't run."""
        return self.connectivity_config or ConnectivityCheckConfig()
