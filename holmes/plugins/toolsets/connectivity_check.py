import ipaddress
import logging
import os
import socket
import threading
import time
from collections import deque
from typing import Any, ClassVar, Deque, Dict, List, Literal, Optional, Sequence, Tuple, Type

from pydantic import Field, PrivateAttr

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
    is_blocked_ip,
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

# Audit marker for every probe the model gets to perform. tcp_check returns
# distinguishable open / refused / filtered outcomes, which is all that is
# needed to enumerate a network, so each attempt is recorded whether or not it
# is allowed — scanning should never be invisible.
PROBE_AUDIT_PREFIX = "connectivity_check probe"

# Escape hatch for deployments that don't want to build an allowlist: set this
# to opt back into probing any private/internal destination. Metadata and
# loopback stay blocked — see ALLOW_ALL_HOSTS_ENV_VAR in the config field below.
ALLOW_ALL_HOSTS_ENV_VAR = "HOLMES_CONNECTIVITY_CHECK_ALLOW_ALL_HOSTS"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _allow_all_hosts_from_env() -> bool:
    """Default for allow_all_hosts, so it can be flipped without editing config.

    Anything other than an explicit truthy value fails safe to False.
    """
    return os.environ.get(ALLOW_ALL_HOSTS_ENV_VAR, "").strip().lower() in _TRUTHY


def _parse_allowlist(
    entries: Sequence[str],
) -> Tuple[List[Any], List[str]]:
    """Split allowlist entries into IP networks and hostname suffixes.

    An entry is treated as a network if it parses as a CIDR or bare IP
    (``10.96.0.0/12``, ``10.0.0.5``); otherwise as a hostname suffix
    (``db.internal`` also matches ``primary.db.internal``).
    """
    networks: List[Any] = []
    hostnames: List[str] = []
    for raw in entries:
        entry = (raw or "").strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            hostnames.append(entry.lower().lstrip(".").rstrip("."))
    return networks, hostnames


def _destination_allowed(
    host: str, resolved_ips: Sequence[str], entries: Sequence[str]
) -> bool:
    """True if the operator explicitly named this destination.

    Matches on the hostname the model supplied *and* on every address it
    resolves to, so a CIDR entry covers a service named by DNS.
    """
    if not entries:
        return False
    networks, hostnames = _parse_allowlist(entries)

    candidate = host.lower().rstrip(".")
    for name in hostnames:
        if candidate == name or candidate.endswith("." + name):
            return True

    for ip_str in resolved_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if any(ip in network for network in networks):
            return True
    return False


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

        config = self.toolset.effective_config

        def refuse(reason: str) -> StructuredToolResult:
            error_message = f"Refusing to connect to {host}:{port_int}: {reason}"
            logging.warning("%s REFUSED %s:%s — %s", PROBE_AUDIT_PREFIX, host, port_int, reason)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                data={"ok": False, "error": error_message},
                params=params,
            )

        # Resolve first, without applying policy, so the checks below can reason
        # about the actual destination. IP literals are validated rather than
        # resolved, so a hostile resolver cannot disguise one.
        try:
            resolved_ips = validate_host(host, port_int, block_internal_ips=False)
        except SSRFValidationError as e:
            return refuse(str(e))

        # An operator naming a destination is an explicit opt-in: it satisfies
        # the private-range requirement below and, as documented, exempts the
        # destination from the internal-IP block.
        allowlist = config.allowed_hosts
        # allow_all_hosts overrides the allowlist *entirely*: while it is on,
        # entries neither restrict nor exempt. Letting them keep their exemption
        # would mean an entry like '169.254.0.0/16' still opened the metadata
        # range, breaking the guarantee that allow_all_hosts never unblocks it.
        allowlisted = not config.allow_all_hosts and _destination_allowed(
            host, resolved_ips, allowlist
        )
        # The operator has waived the allowlist requirement. This authorizes
        # private/internal destinations only — it never unblocks cloud metadata
        # or loopback, which is what block_internal_ips: false is for.
        authorized = allowlisted or config.allow_all_hosts
        if allowlist and not authorized:
            # Deliberately does not echo the allowlist: its contents are the
            # operator's internal service names, which is exactly the sort of
            # recon an injected prompt would like handed back.
            return refuse(
                "destination is not in the connectivity_check 'allowed_hosts' "
                "allowlist"
            )

        # Resolved addresses are deliberately never echoed in a refusal: doing so
        # would answer "what does this internal name resolve to?" for free. They
        # go to the audit log instead.
        #
        # block_private_ips is the stricter, independent switch — it refuses
        # private destinations outright, so it is enforced even when
        # block_internal_ips is off.
        if config.block_private_ips or config.block_internal_ips:
            for ip_str in resolved_ips:
                try:
                    ip = ipaddress.ip_address(ip_str)
                except ValueError:
                    return refuse("a resolved address could not be parsed")

                if ip.is_private and config.block_private_ips:
                    return refuse(
                        "destination is a private/internal address and "
                        "block_private_ips is set"
                    )

                if not config.block_internal_ips:
                    continue

                # Cloud metadata / loopback / link-local / multicast / reserved:
                # blocked unless the operator named this destination.
                if not allowlisted and is_blocked_ip(ip, allow_private_ips=True):
                    return refuse(
                        "host resolves to a non-routable/internal address, "
                        "which is blocked to prevent SSRF"
                    )

                # ROB-1114: private ranges are the network this tool sits
                # inside, so leaving them open to a model-chosen host/port is a
                # blind internal port scanner. Probing internal services is the
                # tool's purpose, so the fix is to require the operator to name
                # them rather than to block the range.
                if ip.is_private and not authorized:
                    return refuse(
                        "destination is a private/internal address. Such "
                        "destinations must be listed in the "
                        "connectivity_check 'allowed_hosts' setting "
                        "(hostname, IP or CIDR) before they can be probed"
                    )

        allowed, limit_reason = self.toolset.consume_probe_budget()
        if not allowed:
            return refuse(limit_reason)

        target_ip = resolved_ips[0]
        logging.info(
            "%s ALLOWED %s:%s (resolved %s)%s",
            PROBE_AUDIT_PREFIX,
            host,
            port_int,
            target_ip,
            " [allow_all_hosts]" if config.allow_all_hosts else "",
        )
        result = tcp_check(
            host=target_ip,
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
        title="Allowed destinations",
        description=(
            "Destinations the check may target, as hostnames (matching "
            "subdomains too), bare IPs, or CIDRs such as '10.96.0.0/12'. "
            "Private/internal destinations MUST appear here before they can be "
            "probed. Listing a destination is an explicit opt-in and also "
            "exempts it from the internal-IP block, so an operator can "
            "deliberately target a specific endpoint. When non-empty, this is "
            "also an exhaustive allowlist: public destinations outside it are "
            "refused as well."
        ),
    )
    allow_all_hosts: bool = Field(
        default_factory=_allow_all_hosts_from_env,
        title="Allow all hosts (no allowlist required)",
        description=(
            "Opt out of the allowlist requirement so private/internal "
            "destinations can be probed without naming them, at your own risk: "
            "the model then chooses freely among your internal addresses, and "
            "tcp_check's open/refused/filtered outcomes make that a network "
            "scanner. This never unblocks cloud-metadata, loopback or "
            "link-local targets — only block_internal_ips: false does that, "
            "independently of this setting. Any allowed_hosts entries are "
            "ignored entirely while "
            "this is on — they neither restrict nor exempt. Also settable via "
            f"the {ALLOW_ALL_HOSTS_ENV_VAR} "
            "environment variable, so no config change is needed. A warning is "
            "logged at startup whenever it is on."
        ),
    )
    block_internal_ips: bool = Field(
        default=True,
        title="Block internal IPs",
        description=(
            "Enforce the destination policy: cloud-metadata (169.254.0.0/16), "
            "loopback, link-local, multicast and reserved targets are refused, "
            "and private/RFC1918 targets are refused unless listed in "
            "allowed_hosts. Disabling this removes those range checks and lets "
            "the model probe any address (except what block_private_ips still "
            "refuses) — only do so in trusted, isolated environments."
        ),
    )
    block_private_ips: bool = Field(
        default=False,
        title="Block private IPs",
        description=(
            "Refuse private/RFC1918 destinations outright, even ones listed in "
            "allowed_hosts and even when block_internal_ips is off. Off by "
            "default because probing named internal services is the tool's "
            "main purpose; enable to restrict the tool to public hosts only."
        ),
    )
    max_probes: int = Field(
        default=60,
        ge=0,
        title="Max probes per window",
        description=(
            "Cap on allowed probes within probe_window_seconds, to bound "
            "enumeration if a destination allowlist is wide. The counter is "
            "per Holmes process and shared across concurrent investigations, "
            "so keep it comfortably above normal use. Set to 0 to disable."
        ),
    )
    probe_window_seconds: float = Field(
        default=60.0,
        gt=0,
        title="Probe rate-limit window",
        description=(
            "Length of the sliding window used by max_probes, in seconds. Must "
            "be positive: a zero-length window would expire every probe "
            "immediately and silently disable the limit."
        ),
    )


class ConnectivityCheckToolset(Toolset):
    config_classes: ClassVar[list[Type[ConnectivityCheckConfig]]] = [
        ConnectivityCheckConfig
    ]

    connectivity_config: Optional[ConnectivityCheckConfig] = None

    _probe_times: Deque[float] = PrivateAttr(default_factory=deque)
    _probe_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

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

        if self.connectivity_config.allow_all_hosts:
            logging.warning(
                "%s: allow_all_hosts is ON — tcp_check may probe ANY private/"
                "internal address the model picks, and its open/refused/filtered "
                "outcomes make that a usable network scanner if an investigation "
                "reads attacker-controlled text. Cloud-metadata and loopback are "
                "still blocked. Prefer listing the destinations you actually need "
                "in allowed_hosts. (Set via config or %s.)",
                self.name,
                ALLOW_ALL_HOSTS_ENV_VAR,
            )
            if self.connectivity_config.allowed_hosts:
                logging.warning(
                    "%s: allowed_hosts is configured but allow_all_hosts "
                    "overrides it — the entries are ignored entirely. They no "
                    "longer restrict destinations, and they no longer exempt "
                    "one from the cloud-metadata/loopback block either. Turn "
                    "off allow_all_hosts to enforce the allowlist.",
                    self.name,
                )
        return True, ""

    @property
    def effective_config(self) -> ConnectivityCheckConfig:
        """Config with safe defaults even if prerequisites haven't run."""
        return self.connectivity_config or ConnectivityCheckConfig()

    def consume_probe_budget(self) -> Tuple[bool, str]:
        """Take one probe from the sliding-window budget.

        Best-effort throttle, not an access control: the allowlist is what
        stops arbitrary scanning. This only bounds how fast a wide allowlist
        can be swept. The window is per process and shared across concurrent
        investigations.
        """
        config = self.effective_config
        if config.max_probes <= 0:
            return True, ""

        now = time.monotonic()
        # Validation pins this above zero; a zero-length window would evict
        # every entry on the next call and silently disable the limit.
        window = config.probe_window_seconds
        with self._probe_lock:
            while self._probe_times and now - self._probe_times[0] >= window:
                self._probe_times.popleft()
            if len(self._probe_times) >= config.max_probes:
                return False, (
                    f"probe rate limit reached ({config.max_probes} probes per "
                    f"{window:g}s). This limit exists so a wide allowlist cannot "
                    "be swept; raise max_probes if legitimate use needs more"
                )
            self._probe_times.append(now)
        return True, ""
