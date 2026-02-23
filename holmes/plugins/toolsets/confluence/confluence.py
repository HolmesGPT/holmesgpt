import logging
import os
import re
from typing import Any, ClassVar, Dict, Literal, Optional, Tuple, Type, cast
from urllib.parse import urlparse

import requests  # type: ignore
from pydantic import Field, model_validator

from holmes.core.tools import (
    CallablePrerequisite,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.http.http_toolset import (
    AuthConfig,
    EndpointConfig,
    HttpRequest,
    HttpToolset,
    HttpToolsetConfig,
)
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

ATLASSIAN_CLOUD_PATTERN = re.compile(r"https?://[^/]+\.atlassian\.net")
ATLASSIAN_GATEWAY_BASE = "https://api.atlassian.com/ex/confluence"


class ConfluenceConfig(ToolsetConfig):
    """Configuration for Confluence REST API access.

    Supports both Confluence Cloud and Data Center/Server.

    Cloud example:
    ```yaml
    toolsets:
      confluence:
        config:
          api_url: "https://mycompany.atlassian.net"
          user: "user@example.com"
          api_key: "your-atlassian-api-token"
    ```

    Data Center with Personal Access Token:
    ```yaml
    toolsets:
      confluence:
        config:
          api_url: "https://confluence.mycompany.com"
          api_key: "your-personal-access-token"
          auth_type: "bearer"
          api_path_prefix: ""
    ```
    """

    api_url: str = Field(
        title="API URL",
        description="Confluence base URL (e.g., https://mycompany.atlassian.net for Cloud, https://confluence.mycompany.com for Data Center)",
        examples=["https://mycompany.atlassian.net", "https://confluence.mycompany.com"],
    )
    user: Optional[str] = Field(
        default=None,
        title="User",
        description="Confluence user email (Cloud) or username (Data Center). Required for basic auth, not needed for bearer auth.",
        examples=["user@example.com"],
    )
    api_key: str = Field(
        title="API Key",
        description="Atlassian API token (Cloud) or Personal Access Token (Data Center)",
    )
    auth_type: Literal["basic", "bearer"] = Field(
        default="basic",
        title="Auth Type",
        description="Authentication type: 'basic' for Cloud (user + API token) or Data Center (user + password), 'bearer' for Data Center Personal Access Tokens (PAT).",
    )
    api_path_prefix: str = Field(
        default="/wiki",
        title="API Path Prefix",
        description="Path prefix before /rest/api. Cloud uses '/wiki' (default). Data Center typically uses '' (empty string). Set to match your instance's context path.",
        examples=["/wiki", "", "/confluence"],
    )
    cloud_id: Optional[str] = Field(
        default=None,
        title="Cloud ID",
        description=(
            "Atlassian Cloud ID for routing through the API gateway (api.atlassian.com). "
            "Required for scoped API tokens and service accounts on Confluence Cloud. "
            "If not set, it will be auto-detected when needed."
        ),
    )

    @model_validator(mode="after")
    def validate_auth(self) -> "ConfluenceConfig":
        if self.auth_type == "basic" and not self.user:
            raise ValueError(
                "Confluence 'user' is required when auth_type is 'basic'. "
                "For Data Center Personal Access Tokens, set auth_type to 'bearer'."
            )
        return self


class ConfluenceToolset(Toolset):
    """Confluence toolset that auto-detects auth and delegates to the HTTP toolset.

    Accepts simple Confluence config (api_url, user, api_key) and handles:
    - Cloud vs Data Center detection
    - Scoped token / API gateway auto-detection
    - Basic auth vs Bearer token
    - api_path_prefix for different Confluence deployments

    After auth detection, it creates an HttpToolset with the resolved config
    and registers its HTTP request tool.
    """

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
    def confluence_config(self) -> ConfluenceConfig:
        return cast(ConfluenceConfig, self.config)

    # ── Auth detection (unchanged from original) ──

    def _is_cloud_url(self) -> bool:
        return bool(ATLASSIAN_CLOUD_PATTERN.match(self.confluence_config.api_url))

    def _resolve_cloud_id(self) -> Optional[str]:
        """Fetch the Cloud ID from the Atlassian tenant info endpoint."""
        if self.confluence_config.cloud_id:
            return self.confluence_config.cloud_id
        try:
            base = self.confluence_config.api_url.rstrip("/")
            resp = requests.get(f"{base}/_edge/tenant_info", timeout=10)
            resp.raise_for_status()
            cloud_id = resp.json().get("cloudId")
            if cloud_id:
                logger.info("Resolved Atlassian Cloud ID: %s", cloud_id)
            return cloud_id
        except Exception as e:
            logger.debug("Failed to resolve Cloud ID from tenant_info: %s", e)
            return None

    def _activate_gateway(self, cloud_id: str) -> None:
        """Switch all future requests to route through the Atlassian API gateway."""
        self._gateway_base_url = f"{ATLASSIAN_GATEWAY_BASE}/{cloud_id}"
        logger.info(
            "Using Atlassian API gateway: %s (scoped token detected)",
            self._gateway_base_url,
        )

    # ── Health check (probes auth, triggers gateway fallback) ──

    def _build_url(self, path: str) -> str:
        if self._gateway_base_url:
            base = self._gateway_base_url.rstrip("/")
        else:
            base = self.confluence_config.api_url.rstrip("/")
        prefix = self.confluence_config.api_path_prefix.rstrip("/")
        return f"{base}{prefix}{path}"

    def _build_auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json"}
        if self.confluence_config.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.confluence_config.api_key}"
        return headers

    def _build_auth_tuple(self) -> Optional[Tuple[str, str]]:
        if self.confluence_config.auth_type == "basic":
            return (self.confluence_config.user or "", self.confluence_config.api_key)
        return None

    def _probe_request(self, path: str, query_params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Make a direct HTTP request for health check / auth probing."""
        url = self._build_url(path)
        response = requests.get(
            url,
            params=query_params,
            auth=self._build_auth_tuple(),
            headers=self._build_auth_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _perform_health_check(self) -> Tuple[bool, str]:
        if self.confluence_config.cloud_id and self._is_cloud_url():
            self._activate_gateway(self.confluence_config.cloud_id)

        try:
            self._probe_request("/rest/api/space", query_params={"limit": "1"})
            return True, "Confluence API is accessible."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status in (401, 403) and self._is_cloud_url() and not self._gateway_base_url:
                gateway_ok, gateway_msg = self._try_gateway_fallback()
                if gateway_ok:
                    return True, gateway_msg
            if status == 401:
                return False, f"Confluence authentication failed. Check user/api_key. HTTP {status}: {e.response.text}"
            if status == 403:
                return False, f"Confluence access denied. Check permissions. HTTP {status}: {e.response.text}"
            return False, f"Confluence API error: HTTP {status}: {e.response.text}"
        except requests.exceptions.ConnectionError as e:
            return False, f"Failed to connect to Confluence at {self.confluence_config.api_url}: {e}"
        except requests.exceptions.Timeout:
            return False, "Confluence health check timed out"
        except Exception as e:
            return False, f"Confluence health check failed: {e}"

    def _try_gateway_fallback(self) -> Tuple[bool, str]:
        """Attempt to use the Atlassian API gateway for scoped tokens."""
        cloud_id = self._resolve_cloud_id()
        if not cloud_id:
            return False, "Could not resolve Cloud ID for gateway fallback."

        self._activate_gateway(cloud_id)
        try:
            self._probe_request("/rest/api/space", query_params={"limit": "1"})
            return True, "Confluence API is accessible via Atlassian API gateway (scoped token)."
        except requests.exceptions.HTTPError as e:
            self._gateway_base_url = None
            status = e.response.status_code
            return False, f"Confluence API gateway also failed. HTTP {status}: {e.response.text}"
        except Exception as e:
            self._gateway_base_url = None
            return False, f"Confluence API gateway fallback failed: {e}"

    # ── HTTP toolset delegation ──

    def _build_effective_base_url(self) -> str:
        """Return the base URL that was proven to work during health check."""
        if self._gateway_base_url:
            return self._gateway_base_url.rstrip("/")
        return self.confluence_config.api_url.rstrip("/")

    def _build_endpoint_config(self) -> EndpointConfig:
        """Build an HTTP endpoint config from the resolved auth state."""
        effective_url = self._build_effective_base_url()
        prefix = self.confluence_config.api_path_prefix.rstrip("/")
        parsed = urlparse(effective_url)
        host = parsed.hostname or parsed.netloc

        if self.confluence_config.auth_type == "bearer" or self._gateway_base_url:
            auth = AuthConfig(
                type="bearer",
                token=self.confluence_config.api_key,
            )
        else:
            auth = AuthConfig(
                type="basic",
                username=self.confluence_config.user or "",
                password=self.confluence_config.api_key,
            )

        path_pattern = f"{parsed.path.rstrip('/')}{prefix}/rest/api/*"

        health_check_path = f"{parsed.path.rstrip('/')}{prefix}/rest/api/space?limit=1"
        health_check_url = f"{parsed.scheme}://{parsed.netloc}{health_check_path}"

        return EndpointConfig(
            hosts=[host],
            paths=[path_pattern],
            methods=["GET"],
            auth=auth,
            health_check_url=health_check_url,
        )

    def _build_llm_instructions(self) -> str:
        """Build Confluence-specific LLM instructions with the resolved base URL."""
        effective_url = self._build_effective_base_url()
        prefix = self.confluence_config.api_path_prefix.rstrip("/")
        base_api = f"{effective_url}{prefix}"

        return f"""### Confluence REST API

You can query Confluence using the REST API.

The base URL is: {base_api}

**Endpoints:**

- GET {base_api}/rest/api/space - List spaces. Query params: limit, start, type (global/personal), status (current/archived)
- GET {base_api}/rest/api/space/{{spaceKey}} - Get space details
- GET {base_api}/rest/api/content/{{contentId}} - Get page by ID. Use ?expand=body.storage for body, ?expand=ancestors for parent hierarchy
- GET {base_api}/rest/api/content/{{contentId}}/child/page - Get child pages. Query params: limit, start, expand (e.g. body.storage)
- GET {base_api}/rest/api/content/{{contentId}}/child/comment - Get comments on a page. Query params: limit, start, expand (e.g. body.storage,version)
- GET {base_api}/rest/api/content/search?cql={{query}} - Search using CQL. Query params: limit, start, expand (e.g. body.storage)
- GET {base_api}/rest/api/content?title={{title}}&spaceKey={{spaceKey}}&type=page - Find page by title and space key

**CQL examples:**

- Search by title: `title="Page Title"`
- Search by text content: `text~"search term"`
- Search in a specific space: `space=SPACEKEY AND title~"keyword"`
- Search by label: `label="incident-response"`
- Combine conditions: `space=OPS AND label="runbook" AND text~"database"`

**Extracting page IDs from URLs:**

URL format: https://company.atlassian.net/wiki/spaces/SPACE/pages/12345/Title
The content ID is the numeric part: 12345

**Tips:**

- Use the space listing endpoint to discover available spaces before searching.
- Search using CQL to find pages, then fetch specific pages by content ID for full content.
- Use `?expand=body.storage` to get the full page body. Use `?expand=ancestors` for parent hierarchy.
- For child pages, use `?expand=body.storage` to include content in the response.
- Keep `limit` low for large result sets and use `start` to paginate.
"""

    def _setup_http_tools(self) -> None:
        """Create an HttpToolset with the resolved config and take its tools."""
        endpoint = self._build_endpoint_config()
        http_config = HttpToolsetConfig(endpoints=[endpoint])
        llm_instructions = self._build_llm_instructions()

        http_toolset = HttpToolset(
            name="confluence",
            config=http_config,
            llm_instructions=llm_instructions,
            enabled=True,
        )
        # Run the HTTP toolset's own prerequisites (registers tools, loads instructions)
        ok, msg = http_toolset.prerequisites_callable(http_config.model_dump())
        if not ok:
            raise RuntimeError(f"Failed to initialize HTTP toolset for Confluence: {msg}")

        self.tools = http_toolset.tools
        self.llm_instructions = http_toolset.llm_instructions
