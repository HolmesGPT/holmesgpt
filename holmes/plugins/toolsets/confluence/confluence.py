import logging
from typing import Any, Dict, Optional, Tuple

import requests  # type: ignore
from pydantic import BaseModel, ConfigDict

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
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class ConfluenceConfig(BaseModel):
    """Configuration for Confluence API access.

    Atlassian Cloud with Personal API Token:
    ```yaml
    url: "https://your-company.atlassian.net"
    username: "your-email@example.com"
    api_key: "your_personal_api_token"
    ```

    Atlassian Cloud with Service Account (omit username):
    ```yaml
    url: "https://your-company.atlassian.net"
    api_key: "your_service_account_token"
    ```
    Service account tokens require Confluence scopes:
    read:confluence-content.all, read:confluence-space.summary

    Self-hosted Confluence (Server/Data Center) with PAT:
    ```yaml
    url: "https://confluence.yourcompany.com"
    api_key: "your_personal_access_token"
    ```
    """

    model_config = ConfigDict(extra="allow")

    url: str
    api_key: str
    username: Optional[str] = None  # If set, uses Basic Auth; otherwise Bearer
    cloud_id: Optional[str] = None  # Auto-discovered for Atlassian Cloud
    verify_ssl: bool = True
    timeout: int = 30

    @property
    def is_atlassian_cloud(self) -> bool:
        """Check if this is an Atlassian Cloud instance."""
        return "atlassian.net" in self.url


class ConfluenceToolset(Toolset):
    """Toolset for accessing Confluence pages and spaces."""

    def __init__(self):
        super().__init__(
            name="confluence",
            enabled=False,
            description="Fetch Confluence pages and spaces",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/confluence/",
            icon_url="https://platform.robusta.dev/demos/confluence.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [
            ListConfluenceSpaces(self),
            GetConfluenceSpacePages(self),
            FetchConfluencePage(self),
        ]

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if the Confluence configuration is valid and accessible."""
        try:
            self.config = ConfluenceConfig(**config)

            # For Atlassian Cloud, auto-discover cloud_id if not provided
            if (
                self.confluence_config.is_atlassian_cloud
                and not self.confluence_config.cloud_id
            ):
                cloud_id = self._discover_cloud_id()
                if cloud_id:
                    self.config = ConfluenceConfig(**{**config, "cloud_id": cloud_id})
                    logging.info(f"Auto-discovered Confluence cloud_id: {cloud_id}")

            return self._perform_health_check()
        except Exception as e:
            return False, f"Failed to validate Confluence configuration: {str(e)}"

    def _discover_cloud_id(self) -> Optional[str]:
        """Discover cloud_id from Atlassian Cloud using the tenant_info endpoint.

        The /_edge/tenant_info endpoint is a public endpoint that returns the cloud_id
        without requiring authentication, which works for both personal tokens and
        service account scoped tokens.
        """
        try:
            # Use the public tenant_info endpoint - no auth required
            url = f"{self.confluence_config.url.rstrip('/')}/_edge/tenant_info"
            response = requests.get(
                url,
                headers={"Accept": "application/json"},
                timeout=self.confluence_config.timeout,
                verify=self.confluence_config.verify_ssl,
            )
            response.raise_for_status()
            data = response.json()

            cloud_id = data.get("cloudId")
            if cloud_id:
                return cloud_id
        except Exception as e:
            logging.debug(f"Could not auto-discover cloud_id: {e}")
        return None

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Perform a health check by querying the space list."""
        try:
            api_prefix = self._get_api_path_prefix()
            response = self._make_request(
                "GET", f"{api_prefix}/space", params={"limit": 1}
            )
            if "results" in response:
                mode = "API Gateway" if self._use_api_gateway() else "Direct"
                return True, f"Connected to Confluence successfully ({mode} mode)"
            return False, "Unexpected response from Confluence API"
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return (
                    False,
                    "Confluence authentication failed. Check your API key and username.",
                )
            elif e.response.status_code == 403:
                hint = ""
                if self.confluence_config.is_atlassian_cloud:
                    hint = " For service accounts, ensure the API token has Confluence scopes: read:confluence-content.all, read:confluence-space.summary"
                return False, f"Confluence access denied.{hint}"
            else:
                return (
                    False,
                    f"Confluence API error: {e.response.status_code} - {e.response.text}",
                )
        except requests.exceptions.ConnectionError:
            return False, f"Failed to connect to Confluence at {self._get_base_url()}"
        except requests.exceptions.Timeout:
            return False, "Confluence health check timed out"
        except Exception as e:
            return False, f"Confluence health check failed: {str(e)}"

    @property
    def confluence_config(self) -> ConfluenceConfig:
        return self.config  # type: ignore

    def _use_api_gateway(self) -> bool:
        """Determine if we should use the Atlassian API gateway."""
        # Use API gateway for Atlassian Cloud when we have a cloud_id
        return self.confluence_config.is_atlassian_cloud and bool(
            self.confluence_config.cloud_id
        )

    def _get_base_url(self) -> str:
        """Get the base URL for API requests."""
        if self._use_api_gateway():
            return f"https://api.atlassian.com/ex/confluence/{self.confluence_config.cloud_id}"
        return self.confluence_config.url.rstrip("/")

    def _get_api_path_prefix(self) -> str:
        """Get the API path prefix based on instance type.

        Atlassian Cloud uses /wiki/rest/api/...
        Self-hosted (Server/Data Center) uses /rest/api/...
        """
        if self.confluence_config.is_atlassian_cloud:
            return "wiki/rest/api"
        return "rest/api"

    def get_example_config(self) -> Dict[str, Any]:
        """Return an example configuration for this toolset."""
        return {
            "url": "https://your-company.atlassian.net",
            "username": "{{ env.CONFLUENCE_USERNAME }}",
            "api_key": "{{ env.CONFLUENCE_API_KEY }}",
        }

    def _get_headers(self) -> Dict[str, str]:
        """Build request headers.

        For service accounts (no username), uses Bearer auth in headers.
        For personal tokens (with username), auth is handled by _get_auth().
        """
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # Service accounts use Bearer auth (no username configured)
        if not self.confluence_config.username:
            headers["Authorization"] = f"Bearer {self.confluence_config.api_key}"
        return headers

    def _get_auth(self) -> Optional[Tuple[str, str]]:
        """Return Basic Auth tuple if username is configured (personal tokens)."""
        if self.confluence_config.username:
            return (self.confluence_config.username, self.confluence_config.api_key)
        return None

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to Confluence API.

        Uses the API gateway (api.atlassian.com) for Atlassian Cloud when cloud_id
        is available, otherwise uses the direct URL for on-premise instances.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "wiki/rest/api/space")
            params: Query parameters
            body: Request body (JSON)
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response

        Raises:
            requests.exceptions.HTTPError: For HTTP error responses
        """
        url = f"{self._get_base_url()}/{endpoint.lstrip('/')}"
        timeout = timeout or self.confluence_config.timeout

        response = requests.request(
            method=method,
            url=url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            params=params,
            json=body,
            timeout=timeout,
            verify=self.confluence_config.verify_ssl,
        )
        response.raise_for_status()
        return response.json()


class BaseConfluenceTool(Tool):
    """Base class for Confluence tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: ConfluenceToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    @property
    def toolset(self) -> ConfluenceToolset:
        return self._toolset

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict,
        query_params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> StructuredToolResult:
        """Make a request to Confluence and return structured result.

        Includes full request context in error responses for debugging.
        """
        # Build request context for error reporting
        request_context = {
            "endpoint": endpoint,
            "method": method,
            "query_params": query_params or {},
            "body": body,
            "params": params,
        }

        try:
            data = self._toolset._make_request(
                method=method,
                endpoint=endpoint,
                params=query_params,
                body=body,
                timeout=timeout,
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except requests.exceptions.HTTPError as e:
            # Include full error context: status code, response text, and parsed JSON if available
            status_code = e.response.status_code
            response_text = e.response.text
            error_json = None
            try:
                error_json = e.response.json()
            except Exception:
                pass

            error_detail = {
                "http_status": status_code,
                "response_text": response_text,
                "response_json": error_json,
                "request_context": request_context,
            }

            error_msg = (
                f"Confluence API error for endpoint '{endpoint}': HTTP {status_code}"
            )
            if error_json and "message" in error_json:
                error_msg = f"{error_msg} - {error_json['message']}"
            elif response_text:
                error_msg = f"{error_msg} - {response_text}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=error_msg,
                data=error_detail,
                params=params,
            )
        except requests.exceptions.Timeout:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Confluence request timed out for endpoint '{endpoint}' (timeout: {timeout or self._toolset.confluence_config.timeout}s)",
                data={"request_context": request_context},
                params=params,
            )
        except requests.exceptions.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to connect to Confluence at {self._toolset.confluence_config.url}: {str(e)}",
                data={"request_context": request_context, "connection_error": str(e)},
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error querying Confluence endpoint '{endpoint}': {type(e).__name__}: {str(e)}",
                data={
                    "request_context": request_context,
                    "exception_type": type(e).__name__,
                },
                params=params,
            )


class ListConfluenceSpaces(BaseConfluenceTool):
    """List all spaces in Confluence."""

    def __init__(self, toolset: ConfluenceToolset):
        super().__init__(
            toolset=toolset,
            name="list_confluence_spaces",
            description=(
                "List all spaces in Confluence. Returns space keys, names, and types. "
                "Use the space_key from results to get pages within a specific space."
            ),
            parameters={
                "limit": ToolParameter(
                    description="Maximum number of spaces to return (default: 100)",
                    type="integer",
                    required=False,
                ),
                "start": ToolParameter(
                    description="Starting index for pagination (default: 0)",
                    type="integer",
                    required=False,
                ),
                "type": ToolParameter(
                    description="Filter by space type: 'global' or 'personal'",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:  # noqa: ARG002
        query_params: Dict[str, Any] = {
            "limit": params.get("limit", 100),
            "start": params.get("start", 0),
        }

        if params.get("type"):
            query_params["type"] = params["type"]

        api_prefix = self._toolset._get_api_path_prefix()
        endpoint = f"{api_prefix}/space"
        result = self._make_request("GET", endpoint, params, query_params=query_params)

        # Check for empty results and return NO_DATA with search context
        if result.status == StructuredToolResultStatus.SUCCESS:
            results = result.data.get("results", []) if result.data else []
            if not results:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=None,
                    params=params,
                    error=f"No spaces found. Endpoint: '{endpoint}', query_params: {query_params}",
                )

        return result

    def get_parameterized_one_liner(self, params: Dict) -> str:  # noqa: ARG002
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List spaces"


class GetConfluenceSpacePages(BaseConfluenceTool):
    """Get pages within a specific Confluence space."""

    def __init__(self, toolset: ConfluenceToolset):
        super().__init__(
            toolset=toolset,
            name="get_confluence_space_pages",
            description=(
                "Get pages within a specific Confluence space. Returns page IDs, titles, and metadata. "
                "Use the page ID with fetch_confluence_page to get full page content."
            ),
            parameters={
                "space_key": ToolParameter(
                    description="The space key (e.g., 'DEV', 'DOCS'). Use list_confluence_spaces to find available space keys.",
                    type="string",
                    required=True,
                ),
                "limit": ToolParameter(
                    description="Maximum number of pages to return (default: 25, max: 100)",
                    type="integer",
                    required=False,
                ),
                "start": ToolParameter(
                    description="Starting index for pagination (default: 0)",
                    type="integer",
                    required=False,
                ),
                "title": ToolParameter(
                    description="Filter pages by title (partial match)",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:  # noqa: ARG002
        space_key = params["space_key"]

        query_params: Dict[str, Any] = {
            "limit": params.get("limit", 25),
            "start": params.get("start", 0),
            "expand": "metadata.labels",
        }

        if params.get("title"):
            query_params["title"] = params["title"]

        api_prefix = self._toolset._get_api_path_prefix()
        endpoint = f"{api_prefix}/space/{space_key}/content/page"
        result = self._make_request("GET", endpoint, params, query_params=query_params)

        # Check for empty results and return NO_DATA with search context
        if result.status == StructuredToolResultStatus.SUCCESS:
            results = result.data.get("results", []) if result.data else []
            if not results:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=None,
                    params=params,
                    error=f"No pages found in space '{space_key}'. Endpoint: '{endpoint}', query_params: {query_params}",
                )

        return result

    def get_parameterized_one_liner(self, params: Dict) -> str:
        space_key = params.get("space_key", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Pages in {space_key}"


class FetchConfluencePage(BaseConfluenceTool):
    """Fetch a Confluence page by its ID."""

    def __init__(self, toolset: ConfluenceToolset):
        super().__init__(
            toolset=toolset,
            name="fetch_confluence_page",
            description=(
                "Fetch a Confluence page by its page ID. Returns full page content including body. "
                "Use get_confluence_space_pages to find page IDs within a space."
            ),
            parameters={
                "page_id": ToolParameter(
                    description="The Confluence page ID. Use get_confluence_space_pages to find page IDs.",
                    type="string",
                    required=True,
                ),
                "expand": ToolParameter(
                    description=(
                        "Comma-separated list of properties to expand. "
                        "Default: 'body.storage'. Options: body.storage, body.view, version, ancestors, children, history"
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:  # noqa: ARG002
        page_id = params["page_id"]
        expand = params.get("expand", "body.storage")

        query_params = {"expand": expand}

        api_prefix = self._toolset._get_api_path_prefix()
        endpoint = f"{api_prefix}/content/{page_id}"
        return self._make_request("GET", endpoint, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        page_id = params.get("page_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Fetch page {page_id}"
