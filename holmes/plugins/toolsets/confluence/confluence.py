import logging
import re
from typing import Any, ClassVar, Dict, Literal, Optional, Tuple, Type
from urllib.parse import urlparse

import requests  # type: ignore
from pydantic import Field, model_validator

from holmes.core.tools import CallablePrerequisite, Toolset, ToolsetTag
from holmes.plugins.toolsets.http.http_toolset import (
    AuthConfig,
    EndpointConfig,
    HttpToolset,
    HttpToolsetConfig,
)
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

ATLASSIAN_CLOUD_PATTERN = re.compile(r"https?://[^/]+\.atlassian\.net")
ATLASSIAN_GATEWAY_BASE = "https://api.atlassian.com/ex/confluence"


class ConfluenceConfig(ToolsetConfig):
    """Configuration for Confluence REST API access (Cloud and Data Center)."""

    api_url: str = Field(
        description="Confluence base URL (e.g., https://mycompany.atlassian.net)",
    )
    user: Optional[str] = Field(
        default=None,
        description="User email (Cloud) or username (Data Center). Required for basic auth.",
    )
    api_key: str = Field(
        description="Atlassian API token (Cloud) or Personal Access Token (Data Center)",
    )
    auth_type: Literal["basic", "bearer"] = Field(
        default="basic",
        description="'basic' for Cloud or Data Center user+password, 'bearer' for Data Center PATs.",
    )
    api_path_prefix: str = Field(
        default="/wiki",
        description="Path prefix before /rest/api. Cloud uses '/wiki', Data Center typically ''.",
    )
    cloud_id: Optional[str] = Field(
        default=None,
        description="Atlassian Cloud ID for the API gateway. Auto-detected when needed.",
    )

    @model_validator(mode="after")
    def validate_auth(self) -> "ConfluenceConfig":
        if self.auth_type == "basic" and not self.user:
            raise ValueError("'user' is required when auth_type is 'basic'. For PATs, set auth_type to 'bearer'.")
        return self


class ConfluenceToolset(Toolset):
    """Confluence toolset that auto-detects auth and delegates to the HTTP toolset."""

    config_classes: ClassVar[list[Type[ConfluenceConfig]]] = [ConfluenceConfig]

    def __init__(self) -> None:
        super().__init__(
            name="confluence",
            description="Fetch and search Confluence pages",
            icon_url="https://platform.robusta.dev/demos/confluence.svg",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self._gateway_base_url: Optional[str] = None

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = ConfluenceConfig(**config)
            self._gateway_base_url = None

            ok, msg = self._perform_health_check()
            if not ok:
                return False, msg

            self._setup_http_tools()
            return True, msg
        except Exception as e:
            return False, f"Failed to validate Confluence configuration: {e}"

    @property
    def _conf(self) -> ConfluenceConfig:
        return self.config  # type: ignore[return-value]

    # ── Cloud detection & gateway ──

    def _is_cloud_url(self) -> bool:
        return bool(ATLASSIAN_CLOUD_PATTERN.match(self._conf.api_url))

    def _resolve_cloud_id(self) -> Optional[str]:
        if self._conf.cloud_id:
            return self._conf.cloud_id
        try:
            resp = requests.get(f"{self._conf.api_url.rstrip('/')}/_edge/tenant_info", timeout=10)
            resp.raise_for_status()
            cloud_id = resp.json().get("cloudId")
            if cloud_id:
                logger.info("Resolved Atlassian Cloud ID: %s", cloud_id)
            return cloud_id
        except Exception as e:
            logger.debug("Failed to resolve Cloud ID: %s", e)
            return None

    def _activate_gateway(self, cloud_id: str) -> None:
        self._gateway_base_url = f"{ATLASSIAN_GATEWAY_BASE}/{cloud_id}"
        logger.info("Using Atlassian API gateway: %s", self._gateway_base_url)

    # ── Health check ──

    def _probe_request(self, path: str, query_params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Direct HTTP request for health-check probing."""
        base = (self._gateway_base_url or self._conf.api_url).rstrip("/")
        prefix = self._conf.api_path_prefix.rstrip("/")
        url = f"{base}{prefix}{path}"

        headers: Dict[str, str] = {"Accept": "application/json"}
        auth: Optional[Tuple[str, str]] = None
        if self._conf.auth_type == "bearer" or self._gateway_base_url:
            headers["Authorization"] = f"Bearer {self._conf.api_key}"
        else:
            auth = (self._conf.user or "", self._conf.api_key)

        response = requests.get(url, params=query_params, auth=auth, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def _perform_health_check(self) -> Tuple[bool, str]:
        if self._conf.cloud_id and self._is_cloud_url():
            self._activate_gateway(self._conf.cloud_id)

        try:
            self._probe_request("/rest/api/space", query_params={"limit": "1"})
            return True, "Confluence API is accessible."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status in (401, 403) and self._is_cloud_url() and not self._gateway_base_url:
                ok, msg = self._try_gateway_fallback()
                if ok:
                    return True, msg
            return False, f"Confluence API error: HTTP {status}: {e.response.text}"
        except requests.exceptions.ConnectionError as e:
            return False, f"Failed to connect to Confluence at {self._conf.api_url}: {e}"
        except requests.exceptions.Timeout:
            return False, "Confluence health check timed out"
        except Exception as e:
            return False, f"Confluence health check failed: {e}"

    def _try_gateway_fallback(self) -> Tuple[bool, str]:
        cloud_id = self._resolve_cloud_id()
        if not cloud_id:
            return False, "Could not resolve Cloud ID for gateway fallback."

        self._activate_gateway(cloud_id)
        try:
            self._probe_request("/rest/api/space", query_params={"limit": "1"})
            return True, "Confluence API is accessible via Atlassian API gateway (scoped token)."
        except Exception as e:
            self._gateway_base_url = None
            return False, f"Confluence API gateway fallback failed: {e}"

    # ── HTTP toolset delegation ──

    def _effective_base(self) -> str:
        return (self._gateway_base_url or self._conf.api_url).rstrip("/")

    def _build_endpoint_config(self) -> EndpointConfig:
        effective_url = self._effective_base()
        prefix = self._conf.api_path_prefix.rstrip("/")
        parsed = urlparse(effective_url)
        host = parsed.hostname or parsed.netloc
        root = parsed.path.rstrip("/")

        if self._conf.auth_type == "bearer" or self._gateway_base_url:
            auth = AuthConfig(type="bearer", token=self._conf.api_key)
        else:
            auth = AuthConfig(type="basic", username=self._conf.user or "", password=self._conf.api_key)

        return EndpointConfig(
            hosts=[host],
            paths=[f"{root}{prefix}/rest/api/*"],
            methods=["GET"],
            auth=auth,
            health_check_url=f"{parsed.scheme}://{parsed.netloc}{root}{prefix}/rest/api/space?limit=1",
        )

    def _build_llm_instructions(self) -> str:
        base = f"{self._effective_base()}{self._conf.api_path_prefix.rstrip('/')}"
        api = f"{base}/rest/api"
        return f"""### Confluence REST API

Base URL: {base}

**Endpoints:**

- GET {api}/space - List spaces (params: limit, start, type, status)
- GET {api}/space/{{spaceKey}} - Get space details
- GET {api}/content/{{contentId}}?expand=body.storage - Get page by ID
- GET {api}/content/{{contentId}}?expand=ancestors - Get page with parent hierarchy
- GET {api}/content/{{contentId}}/child/page?expand=body.storage - Get child pages
- GET {api}/content/{{contentId}}/child/comment?expand=body.storage - Get comments
- GET {api}/content/search?cql={{query}}&expand=body.storage - Search using CQL
- GET {api}/content?title={{title}}&spaceKey={{spaceKey}}&type=page&expand=body.storage - Find page by title

**CQL examples:** `title="Page Title"`, `text~"search term"`, `space=OPS AND label="runbook"`

**Page IDs from URLs:** `https://company.atlassian.net/wiki/spaces/SPACE/pages/12345/Title` → content ID is `12345`

**Tips:** Always use `expand=body.storage` to get page content. Use CQL search to find pages, then fetch by ID for full content.
"""

    def _setup_http_tools(self) -> None:
        endpoint = self._build_endpoint_config()
        http_config = HttpToolsetConfig(endpoints=[endpoint])
        http_toolset = HttpToolset(
            name="confluence",
            config=http_config,
            llm_instructions=self._build_llm_instructions(),
            enabled=True,
        )
        ok, msg = http_toolset.prerequisites_callable(http_config.model_dump())
        if not ok:
            raise RuntimeError(f"Failed to initialize HTTP toolset for Confluence: {msg}")

        self.tools = http_toolset.tools
        self.llm_instructions = http_toolset.llm_instructions
