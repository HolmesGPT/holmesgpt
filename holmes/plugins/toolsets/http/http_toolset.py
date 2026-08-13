import fnmatch
import json
import logging
import os
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    List,
    Literal,
    Optional,
    Tuple,
    Type,
)
from urllib.parse import urljoin, urlparse

import requests  # type: ignore
from pydantic import BaseModel, Field, PrivateAttr, model_validator
from requests.auth import HTTPDigestAuth  # type: ignore

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
    ToolsetType,
)
from holmes.plugins.toolsets.internet.ssrf import (
    SSRFValidationError,
    build_pinned_adapter,
    validate_url,
)
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.utils.header_rendering import render_header_templates
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
SUPPORTED_SCHEMES = ("http", "https")
SCHEME_DEFAULT_PORTS = {"http": 80, "https": 443}

# Redirects are never followed blindly: the whitelist is enforced against the
# ORIGINAL url only, so an allowed host that can be made to emit a 30x (open
# redirect, attacker-controlled path/param, compromised upstream) would
# otherwise pivot the request to cloud metadata or an in-cluster service. Every
# hop is re-validated against match_endpoint() instead.
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

# Bound the manual redirect chain, mirroring requests' default.
MAX_REDIRECTS = 5

# Headers that carry credentials and must never survive a cross-origin redirect.
# requests' own rebuild_auth() only strips 'Authorization' and only when the
# HOSTNAME changes, so operator-configured header auth (e.g. X-API-Key) and
# same-host/different-port hops would otherwise leak the credential.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
    }
)


def _origin(url: str) -> Tuple[str, str, Optional[int]]:
    """(scheme, host, effective port) — the origin a credential is scoped to."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = SCHEME_DEFAULT_PORTS.get(scheme)
    return scheme, (parsed.hostname or "").lower(), port


def _strip_credentials(
    headers: Dict[str, str], endpoint: Optional["EndpointConfig"]
) -> Dict[str, str]:
    """Drop credential-bearing headers before crossing an origin boundary.

    In addition to the well-known credential headers, the header name the
    matched endpoint configured for `auth: {type: header}` is dropped, since it
    is a secret regardless of what the operator named it.
    """
    drop = set(SENSITIVE_HEADERS)
    if endpoint is not None and endpoint.auth.type == "header" and endpoint.auth.name:
        drop.add(endpoint.auth.name.lower())
    return {k: v for k, v in headers.items() if k.lower() not in drop}


@dataclass(frozen=True)
class ParsedHostPattern:
    """A host whitelist entry parsed into its constraints.

    scheme=None means any scheme is allowed; otherwise only that scheme.
    ports=None means any port is allowed; otherwise the request port (or
    scheme default if request omits the port) must be in the set.
    host_pattern is either an exact lowercased host or a leading-wildcard
    pattern starting with '*.'.
    """

    scheme: Optional[str]
    host_pattern: str
    ports: Optional[FrozenSet[int]]


def _parse_host_pattern(entry: str) -> ParsedHostPattern:
    """Parse a single 'hosts' entry into a ParsedHostPattern.

    Accepts:
      bare hostname           -> any scheme, any port
      *.example.com           -> any scheme, any port
      host:port               -> any scheme, that port only
      http(s)://host          -> that scheme, scheme-default port
      http(s)://host:port     -> that scheme, that port only
      http(s)://*.host        -> that scheme, scheme-default port
      http(s)://host:*        -> that scheme, any port
      *.host:*                -> any scheme, any port (explicit wildcard)
      [ipv6]:port / scheme://[ipv6]:port  -> IPv6 forms
    """
    if not isinstance(entry, str):
        raise ValueError(f"Host pattern must be a string, got {type(entry).__name__}")
    s = entry.strip()
    if not s:
        raise ValueError("Empty host pattern")

    scheme: Optional[str] = None
    if "://" in s:
        scheme_part, _, rest = s.partition("://")
        scheme = scheme_part.lower()
        if scheme not in SUPPORTED_SCHEMES:
            raise ValueError(
                f"Unsupported scheme {scheme!r} in host pattern {entry!r}. "
                f"Supported schemes: {', '.join(SUPPORTED_SCHEMES)}."
            )
        s = rest

    for sep in ("/", "?", "#"):
        if sep in s:
            s = s.split(sep, 1)[0]

    if "@" in s:
        s = s.rsplit("@", 1)[-1]

    host_pattern: str
    port_str: Optional[str] = None

    if s.startswith("["):
        close = s.find("]")
        if close == -1:
            raise ValueError(f"Unclosed '[' in host pattern: {entry!r}")
        host_pattern = s[1:close]
        rest_after = s[close + 1 :]
        if rest_after.startswith(":"):
            port_str = rest_after[1:]
        elif rest_after:
            raise ValueError(
                f"Unexpected content after IPv6 host in {entry!r}: {rest_after!r}"
            )
    elif s.count(":") == 1:
        host_pattern, _, port_str = s.partition(":")
    else:
        host_pattern = s

    if not host_pattern:
        raise ValueError(f"Empty host in pattern: {entry!r}")

    if "*" in host_pattern and not host_pattern.startswith("*."):
        raise ValueError(
            f"Wildcards in host patterns must be a leading '*.' "
            f"(e.g. '*.example.com'). Got: {entry!r}"
        )

    host_pattern = host_pattern.lower()

    ports: Optional[FrozenSet[int]]
    if port_str is None:
        if scheme is not None:
            ports = frozenset({SCHEME_DEFAULT_PORTS[scheme]})
        else:
            ports = None
    elif port_str == "*":
        ports = None
    elif port_str == "":
        raise ValueError(f"Empty port in host pattern: {entry!r}")
    else:
        try:
            port_num = int(port_str)
        except ValueError as err:
            raise ValueError(
                f"Invalid port {port_str!r} in host pattern {entry!r}"
            ) from err
        if not (1 <= port_num <= 65535):
            raise ValueError(
                f"Port {port_num} out of range (1-65535) in host pattern {entry!r}"
            )
        ports = frozenset({port_num})

    return ParsedHostPattern(scheme=scheme, host_pattern=host_pattern, ports=ports)


class AuthConfig(BaseModel):
    type: Literal["none", "basic", "bearer", "header", "digest"] = "none"
    # For basic/digest auth
    username: Optional[str] = None
    password: Optional[str] = None
    # For bearer auth
    token: Optional[str] = None
    # For custom header auth
    name: Optional[str] = None
    value: Optional[str] = None

    @model_validator(mode="after")
    def validate_auth_fields(self) -> "AuthConfig":
        if self.type == "basic":
            if not self.username or not self.password:
                raise ValueError("Basic auth requires 'username' and 'password'")
        elif self.type == "digest":
            if not self.username or not self.password:
                raise ValueError("Digest auth requires 'username' and 'password'")
        elif self.type == "bearer":
            if not self.token:
                raise ValueError("Bearer auth requires 'token'")
        elif self.type == "header":
            if not self.name or not self.value:
                raise ValueError("Header auth requires 'name' and 'value'")
        return self


class EndpointConfig(BaseModel):
    hosts: List[str] = Field(
        description=(
            "Allowed host patterns. Each entry is one of:\n"
            "  - bare hostname ('api.example.com') -- any scheme, any port\n"
            "  - leading wildcard ('*.example.com') -- any scheme, any port\n"
            "  - host with port ('host.example.com:8080') -- any scheme, that port\n"
            "  - URL with scheme ('https://api.example.com') -- that scheme, scheme-default port\n"
            "  - URL with scheme + port ('https://api.example.com:8443') -- that scheme, that port\n"
            "  - explicit any-port wildcard ('https://*.example.com:*') -- that scheme, any port"
        ),
        examples=[
            [
                "api.example.com",
                "*.internal.example.com",
                "https://jenkins.example.com:8080",
            ]
        ],
    )
    paths: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Allowed path patterns (glob-style). Default allows all paths.",
        examples=[["/api/*", "/v2/*"]],
    )
    methods: List[str] = Field(
        default_factory=lambda: ["GET"],
        description="Allowed HTTP methods. Default is GET only.",
        examples=[["GET", "POST"]],
    )
    auth: AuthConfig = Field(
        default_factory=AuthConfig,
        description="Authentication configuration for this endpoint.",
    )
    health_check_url: Optional[str] = Field(
        default=None,
        description="Optional URL to verify auth at initialization time.",
        examples=["https://api.example.com/health"],
    )

    _parsed_hosts: List[ParsedHostPattern] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _parse_and_cache_hosts(self) -> "EndpointConfig":
        # Parsing both validates entries (raises on malformed) and populates the cache.
        self._parsed_hosts = [_parse_host_pattern(h) for h in self.hosts]
        return self

    def parsed_hosts(self) -> List[ParsedHostPattern]:
        return self._parsed_hosts

    def get_methods(self) -> List[str]:
        return [m.upper() for m in self.methods]


class HttpToolsetConfig(ToolsetConfig):
    endpoints: List[EndpointConfig] = Field(default_factory=list)
    verify_ssl: bool = True
    timeout_seconds: int = 30
    default_headers: Dict[str, str] = Field(default_factory=dict)
    extra_headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Extra HTTP headers rendered via Jinja2 templates. "
        "Supports request context (e.g. {{ request_context.headers['X-Tenant-Id'] }}) and env vars (e.g. {{ env.MY_TOKEN }}).",
    )
    client_cert_path: Optional[str] = Field(
        default=None,
        description="Path to client certificate file for mTLS authentication.",
    )
    client_key_path: Optional[str] = Field(
        default=None,
        description="Path to client private key file for mTLS. If not set, the cert file is assumed to contain both cert and key.",
    )
    block_internal_ips: bool = Field(
        default=False,
        description="Reject requests whose host resolves to a loopback, link-local "
        "(incl. 169.254.169.254 cloud metadata), private or reserved address, and pin "
        "the connection to the validated IP to defeat DNS rebinding. Defaults to False "
        "because whitelisted endpoints are commonly in-cluster services "
        "(e.g. http://prometheus.monitoring.svc:9090). Enable it when every configured "
        "endpoint is a public host.",
    )


class HttpToolset(Toolset):
    """Generic HTTP toolset for making requests to whitelisted endpoints.

    Supports multiple instances via `type: http` in config.
    Each instance gets its own tool name, endpoints, and LLM instructions.
    """

    config_classes: ClassVar[List[Type[HttpToolsetConfig]]] = [HttpToolsetConfig]

    def __init__(self, name: str = "http", **kwargs: Any):
        llm_instructions = kwargs.pop("llm_instructions", None)
        config = kwargs.pop("config", None)
        enabled = kwargs.pop("enabled", False)
        kwargs.pop("type", None)

        description = kwargs.pop("description", None)
        if not description:
            if name == "http":
                description = "Generic HTTP client for making requests to whitelisted API endpoints"
            else:
                description = f"HTTP client for {name} API"

        super().__init__(
            name=name,
            description=description,
            type=ToolsetType.HTTP,
            icon_url="https://cdn-icons-png.flaticon.com/512/2165/2165004.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/http/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            enabled=enabled,
            **kwargs,
        )
        self._http_config: Optional[HttpToolsetConfig] = None

        if config:
            self.config = config

        self._user_llm_instructions = llm_instructions

    def _derive_tool_name(self) -> str:
        return self.name.replace("/", "_").replace("-", "_") + "_request"

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self._http_config = HttpToolsetConfig(**config)
            self.config = self._http_config

            if not self._http_config.endpoints:
                return (
                    False,
                    "No endpoints configured. Add at least one endpoint with hosts and auth.",
                )

            if self._http_config.client_cert_path and not os.path.isfile(
                self._http_config.client_cert_path
            ):
                return (
                    False,
                    f"Client certificate file not found: {self._http_config.client_cert_path}",
                )
            if self._http_config.client_key_path and not os.path.isfile(
                self._http_config.client_key_path
            ):
                return (
                    False,
                    f"Client key file not found: {self._http_config.client_key_path}",
                )

            for i, endpoint in enumerate(self._http_config.endpoints):
                if not endpoint.hosts:
                    return False, f"Endpoint {i} has no hosts configured."

                for method in endpoint.get_methods():
                    if method not in ALL_METHODS:
                        return (
                            False,
                            f"Endpoint {i} has invalid method: {method}. Allowed: {ALL_METHODS}",
                        )

            # Perform health checks
            for i, endpoint in enumerate(self._http_config.endpoints):
                if endpoint.health_check_url:
                    success, error_msg = self._check_endpoint_health(endpoint, i)
                    if not success:
                        return False, error_msg

            tool_name = self._derive_tool_name()

            endpoints_summary = ", ".join(
                f"{ep.hosts[0] if len(ep.hosts) == 1 else f'{len(ep.hosts)} hosts'}"
                for ep in self._http_config.endpoints[:2]
            )
            if len(self._http_config.endpoints) > 2:
                endpoints_summary += f", +{len(self._http_config.endpoints) - 2} more"

            if self.name == "http":
                tool_description = f"Make HTTP requests to whitelisted API endpoints ({endpoints_summary})"
            else:
                tool_description = (
                    f"Make HTTP requests to {self.name} API ({endpoints_summary})"
                )

            self.tools = [
                HttpRequest(
                    self, tool_name=tool_name, tool_description=tool_description
                )
            ]

            self._load_llm_instructions_from_file(
                os.path.dirname(__file__), "instructions.jinja2"
            )

            if self._user_llm_instructions:
                self.llm_instructions = (
                    (self.llm_instructions or "")
                    + "\n\n## API-Specific Instructions\n\n"
                    + self._user_llm_instructions
                )

            endpoint_count = len(self._http_config.endpoints)
            host_count = sum(len(ep.hosts) for ep in self._http_config.endpoints)
            return (
                True,
                f"HTTP toolset '{self.name}' configured with {endpoint_count} endpoint(s) covering {host_count} host pattern(s).",
            )

        except Exception as e:
            return False, f"Invalid HTTP configuration: {e}"

    def _build_curl_command(self, endpoint: EndpointConfig, url: str) -> str:
        parts = ["curl", "-v"]

        auth = endpoint.auth
        if auth.type == "basic":
            parts.append('-u "$USERNAME:$PASSWORD"')
        elif auth.type == "digest":
            parts.append('--digest -u "$USERNAME:$PASSWORD"')
        elif auth.type == "bearer":
            parts.append('-H "Authorization: Bearer $TOKEN"')
        elif auth.type == "header" and auth.name:
            parts.append(f'-H "{auth.name}: $SECRET"')

        if self._http_config:
            if self._http_config.client_cert_path:
                parts.append(f'--cert "{self._http_config.client_cert_path}"')
            if self._http_config.client_key_path:
                parts.append(f'--key "{self._http_config.client_key_path}"')

        parts.append(f'"{url}"')
        return " ".join(parts)

    def _check_endpoint_health(
        self, endpoint: EndpointConfig, endpoint_index: int
    ) -> Tuple[bool, str]:
        url = endpoint.health_check_url
        if not url:
            return True, ""

        curl_cmd = self._build_curl_command(endpoint, url)

        try:
            headers = self.build_headers(endpoint)
            auth_obj = self.get_request_auth(endpoint)

            request_kwargs: Dict[str, Any] = {
                "headers": headers,
                "auth": auth_obj,
                "timeout": 10,
                "verify": self._http_config.verify_ssl if self._http_config else True,
            }
            cert = self.get_client_cert()
            if cert:
                request_kwargs["cert"] = cert

            # The health-check URL is operator-configured, but it can still be
            # open-redirected, which would replay the endpoint's credentials
            # elsewhere. It is allowed to sit outside the endpoint's `paths`
            # whitelist, so hops are constrained to its own origin rather than
            # to match_endpoint().
            response, redirect_error = self.request_with_validated_redirects(
                "GET",
                url,
                request_kwargs,
                endpoint=endpoint,
                check_redirect_target=self._same_origin_hop(url),
            )
            if redirect_error or response is None:
                return (
                    False,
                    f"Health check failed for endpoint {endpoint_index} ({url}): "
                    f"{redirect_error or 'request refused'}\n"
                    f"To troubleshoot, run: {curl_cmd}",
                )

            if response.ok:
                logger.info(f"Health check passed for endpoint {endpoint_index}: {url}")
                return True, ""
            else:
                return (
                    False,
                    f"Health check failed for endpoint {endpoint_index} ({url}): "
                    f"HTTP {response.status_code} - {response.text[:200]}\n"
                    f"To troubleshoot, run: {curl_cmd}",
                )

        except requests.exceptions.ConnectionError as e:
            return (
                False,
                f"Health check failed for endpoint {endpoint_index} ({url}): "
                f"Connection error - {e}\n"
                f"To troubleshoot, run: {curl_cmd}",
            )
        except requests.exceptions.Timeout:
            return (
                False,
                f"Health check failed for endpoint {endpoint_index} ({url}): "
                f"Request timed out\n"
                f"To troubleshoot, run: {curl_cmd}",
            )
        except Exception as e:
            return (
                False,
                f"Health check failed for endpoint {endpoint_index} ({url}): {e}\n"
                f"To troubleshoot, run: {curl_cmd}",
            )

    @property
    def http_config(self) -> HttpToolsetConfig:
        if self._http_config is None:
            raise RuntimeError(
                "HTTP toolset not configured. Call prerequisites_callable first."
            )
        return self._http_config

    def match_endpoint(
        self, url: str
    ) -> Tuple[Optional[EndpointConfig], Optional[str]]:
        try:
            parsed = urlparse(url)
        except Exception as e:
            return None, f"Invalid URL: {e}"

        if not parsed.scheme or not parsed.netloc:
            return None, f"Invalid URL format: {url}"

        host = (parsed.hostname or parsed.netloc).lower()
        scheme = parsed.scheme.lower()
        path = parsed.path or "/"
        try:
            req_port = parsed.port
        except ValueError:
            return None, f"Invalid port in URL: {url}"

        for endpoint in self.http_config.endpoints:
            for ph in endpoint.parsed_hosts():
                if self._match_pattern(scheme, host, req_port, ph):
                    if self._match_path(path, endpoint.paths):
                        return endpoint, None

        port_display = req_port if req_port is not None else "(default)"
        return (
            None,
            f"URL not in whitelist. Scheme '{scheme}', host '{host}', "
            f"port {port_display}, path '{path}' does not match any configured endpoint.",
        )

    @staticmethod
    def _match_pattern(
        req_scheme: str,
        req_host: str,
        req_port: Optional[int],
        pattern: ParsedHostPattern,
    ) -> bool:
        if pattern.scheme is not None and req_scheme != pattern.scheme:
            return False

        if pattern.host_pattern.startswith("*."):
            if not req_host.endswith(pattern.host_pattern[1:]):
                return False
        else:
            if req_host != pattern.host_pattern:
                return False

        if pattern.ports is not None:
            effective_port = req_port
            if effective_port is None:
                effective_port = SCHEME_DEFAULT_PORTS.get(req_scheme)
            if effective_port is None or effective_port not in pattern.ports:
                return False

        return True

    def _match_path(self, path: str, patterns: List[str]) -> bool:
        for pattern in patterns:
            if pattern == "*":
                return True
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    def is_method_allowed(self, method: str, endpoint: EndpointConfig) -> bool:
        return method.upper() in endpoint.get_methods()

    def build_headers(
        self, endpoint: EndpointConfig, extra_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self._http_config:
            headers.update(self._http_config.default_headers)

        auth = endpoint.auth
        if auth.type == "bearer":
            headers["Authorization"] = f"Bearer {auth.token}"
        elif auth.type == "header":
            if auth.name and auth.value:
                headers[auth.name] = auth.value

        if extra_headers:
            headers.update(extra_headers)

        return headers

    def get_request_auth(self, endpoint: EndpointConfig) -> Optional[Any]:
        if endpoint.auth.username and endpoint.auth.password:
            if endpoint.auth.type == "basic":
                return (endpoint.auth.username, endpoint.auth.password)
            if endpoint.auth.type == "digest":
                return HTTPDigestAuth(endpoint.auth.username, endpoint.auth.password)
        return None

    def _send(
        self, method: str, url: str, request_kwargs: Dict[str, Any]
    ) -> requests.Response:
        """Send a single hop, never following redirects itself.

        When `block_internal_ips` is enabled the host is resolved and validated
        first and the socket is pinned to that exact IP, so a DNS rebind between
        validation and connection cannot swap in an internal address.
        """
        kwargs = dict(request_kwargs)
        kwargs["allow_redirects"] = False

        if not (self._http_config and self._http_config.block_internal_ips):
            return requests.request(method, url, **kwargs)

        pinned_ip = validate_url(url, block_internal_ips=True)[0]
        session = requests.Session()
        adapter = build_pinned_adapter(pinned_ip)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        try:
            return session.request(method, url, **kwargs)
        finally:
            session.close()

    def _same_origin_hop(
        self, origin_url: str
    ) -> Callable[[str], Tuple[Optional["EndpointConfig"], Optional[str]]]:
        """Hop validator that only permits redirects staying on the same origin.

        Used for health checks, whose configured URL is allowed to sit outside
        the endpoint's `paths` whitelist.
        """

        def check(next_url: str) -> Tuple[Optional["EndpointConfig"], Optional[str]]:
            if _origin(next_url) != _origin(origin_url):
                return None, "redirect leaves the origin of the configured URL"
            return None, None

        return check

    def request_with_validated_redirects(
        self,
        method: str,
        url: str,
        request_kwargs: Dict[str, Any],
        endpoint: Optional["EndpointConfig"] = None,
        check_redirect_target: Optional[
            Callable[[str], Tuple[Optional["EndpointConfig"], Optional[str]]]
        ] = None,
    ) -> Tuple[Optional[requests.Response], Optional[str]]:
        """Perform a request, following redirects only to URLs that pass
        `check_redirect_target` (the endpoint whitelist by default).

        `endpoint` is the already-matched endpoint the initial URL belongs to;
        it is used to know which credential header to strip on a cross-origin
        hop. Returns (response, error) — when `error` is set the response is
        never surfaced to the LLM.
        """
        if check_redirect_target is None:
            check_redirect_target = self.match_endpoint

        current_url = url
        current_endpoint = endpoint
        current_method = method
        kwargs = dict(request_kwargs)

        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = self._send(current_method, current_url, kwargs)
            except SSRFValidationError as e:
                return None, f"Refusing to request {current_url}: {e}"

            if response.status_code not in REDIRECT_STATUS_CODES:
                return response, None

            location = response.headers.get("location")
            if not location:
                # A 3xx with no Location is not a redirect we can follow; treat
                # it as the final response.
                return response, None

            next_url = urljoin(current_url, location)
            next_endpoint, next_error = check_redirect_target(next_url)
            if next_error:
                return None, (
                    f"Refusing to follow redirect from {current_url} to {next_url}: "
                    f"{next_error}"
                )

            # 301/302/303 downgrade a non-idempotent method to GET (browser and
            # requests behaviour); 307/308 preserve method and body.
            next_method = current_method
            if response.status_code in (301, 302, 303) and current_method not in (
                "GET",
                "HEAD",
            ):
                next_method = "GET"
                kwargs.pop("data", None)

            if next_endpoint is not None and not self.is_method_allowed(
                next_method, next_endpoint
            ):
                return None, (
                    f"Refusing to follow redirect from {current_url} to {next_url}: "
                    f"method {next_method} not allowed for the redirect target. "
                    f"Allowed methods: {next_endpoint.get_methods()}"
                )

            if _origin(current_url) != _origin(next_url):
                kwargs["headers"] = _strip_credentials(
                    kwargs.get("headers") or {}, current_endpoint
                )
                kwargs["auth"] = None

            current_url = next_url
            current_endpoint = next_endpoint
            current_method = next_method

        return None, (
            f"Refusing to follow more than {MAX_REDIRECTS} redirects starting at {url}"
        )

    def get_client_cert(self) -> Optional[Any]:
        if not self._http_config or not self._http_config.client_cert_path:
            return None
        if self._http_config.client_key_path:
            return (
                self._http_config.client_cert_path,
                self._http_config.client_key_path,
            )
        return self._http_config.client_cert_path


class HttpRequest(Tool, JsonFilterMixin):
    def __init__(
        self,
        toolset: HttpToolset,
        tool_name: str = "http_request",
        tool_description: Optional[str] = None,
    ):
        if not tool_description:
            if toolset.name == "http":
                tool_description = "Make HTTP requests to whitelisted API endpoints"
            else:
                tool_description = f"Make HTTP requests to {toolset.name} API endpoints"

        base_params = {
            "url": ToolParameter(
                description="The full URL to request (must match a whitelisted endpoint)",
                type="string",
                required=True,
            ),
            "method": ToolParameter(
                description="HTTP method (default: GET). Must be allowed by the endpoint configuration.",
                type="string",
                required=False,
            ),
            "body": ToolParameter(
                description="Request body (JSON string) for POST/PUT/PATCH requests",
                type="string",
                required=False,
            ),
            "headers": ToolParameter(
                description="Additional HTTP headers as a JSON object (optional, overrides defaults)",
                type="string",
                required=False,
            ),
        }

        parameters = JsonFilterMixin.extend_parameters(base_params)

        super().__init__(
            name=tool_name,
            description=tool_description,
            parameters=parameters,
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        body = params.get("body")
        extra_headers_str = params.get("headers")

        endpoint, error = self._toolset.match_endpoint(url)
        if error or endpoint is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error or "URL not matched",
                params=params,
                url=url,
            )

        if method not in ALL_METHODS:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unsupported HTTP method: {method}. Supported: {ALL_METHODS}",
                params=params,
                url=url,
            )

        if not self._toolset.is_method_allowed(method, endpoint):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Method {method} not allowed for this endpoint. Allowed methods: {endpoint.get_methods()}",
                params=params,
                url=url,
            )

        extra_headers = None
        if extra_headers_str:
            try:
                extra_headers = json.loads(extra_headers_str)
                if not isinstance(extra_headers, dict):
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error="Headers must be a JSON object, not a list or primitive",
                        params=params,
                        url=url,
                    )
            except json.JSONDecodeError as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"Invalid headers JSON: {e}",
                    params=params,
                    url=url,
                )

        headers = self._toolset.build_headers(endpoint, extra_headers)

        # Merge rendered toolset-level extra_headers
        if self._toolset.http_config.extra_headers:
            rendered_extra = render_header_templates(
                extra_headers=self._toolset.http_config.extra_headers,
                request_context=context.request_context,
                source_name=self._toolset.name,
            )
            if rendered_extra:
                headers.update(rendered_extra)
        auth_obj = self._toolset.get_request_auth(endpoint)
        timeout = self._toolset.http_config.timeout_seconds
        verify_ssl = self._toolset.http_config.verify_ssl

        try:
            request_kwargs: Dict[str, Any] = {
                "headers": headers,
                "auth": auth_obj,
                "timeout": timeout,
                "verify": verify_ssl,
            }
            cert = self._toolset.get_client_cert()
            if cert:
                request_kwargs["cert"] = cert

            if method in ("POST", "PUT", "PATCH") and body:
                request_kwargs["data"] = body

            response, redirect_error = self._toolset.request_with_validated_redirects(
                method, url, request_kwargs, endpoint=endpoint
            )
            if redirect_error or response is None:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=redirect_error or "Request refused",
                    params=params,
                    url=url,
                )

            try:
                data = response.json()
            except Exception:
                data = response.text

            if response.ok:
                result = StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data={"status_code": response.status_code, "body": data},
                    params=params,
                    url=url,
                )
            else:
                result = StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=f"HTTP {response.status_code}: {data}",
                    data={"status_code": response.status_code, "body": data},
                    params=params,
                    url=url,
                )

            return self.filter_result(result, params)

        except requests.exceptions.Timeout:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request timed out after {timeout}s",
                params=params,
                url=url,
            )
        except requests.exceptions.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Connection error: {e}",
                params=params,
                url=url,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Request failed: {e}",
                params=params,
                url=url,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        url = params.get("url", "unknown")
        method = params.get("method", "GET").upper()
        if len(url) > 50:
            url = url[:47] + "..."
        return f"HTTP {method} {url}"
