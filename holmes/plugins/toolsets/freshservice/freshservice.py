import json
import os
import time
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, cast
from urllib.parse import parse_qsl, urljoin

import requests  # type: ignore
from pydantic import BaseModel, Field
from requests.auth import HTTPBasicAuth

from holmes.core.tools import (
    ApprovalRequirement,
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.plugins.toolsets.consts import TOOLSET_CONFIG_MISSING_ERROR
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.plugins.toolsets.utils import (
    is_int,
    is_rfc3339,
    toolset_name_for_one_liner,
    unix_to_rfc3339,
)
from holmes.utils.pydantic_utils import ToolsetConfig

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


class FreshserviceObjectType(BaseModel):
    """Describes one Freshservice object type exposed through the generic tools."""

    path: str  # API path relative to /api/v2, e.g. "tickets" or "solutions/articles"
    plural_key: str  # JSON envelope key for list responses, e.g. "tickets"
    singular_key: str  # JSON envelope key for single-object responses, e.g. "ticket"
    supports_updated_since: bool = False
    # How this object type supports server-side searching:
    #   "filter_endpoint" -> GET {path}/filter?query="<query>"
    #   "filter_param"    -> GET {path}?filter="<query>"
    #   "query_param"     -> GET {path}?query="<query>"
    #   None              -> no server-side search support
    filter_style: Optional[str] = None
    includes: List[str] = []  # documented values for the `include` query param
    sub_resources: List[str] = []  # relations available at {path}/{id}/{relation}
    # Whether the object type supports create/update/delete through the standard
    # REST pattern (POST {path}, PUT {path}/{id}, DELETE {path}/{id}).
    writable: bool = True
    # Relations that can be created/updated/deleted at {path}/{id}/{relation}
    # (e.g. notes, tasks, time_entries, custom object records).
    write_sub_resources: List[str] = []
    notes: str = ""


# Read-only registry of Freshservice API v2 object types.
# Reference: https://api.freshservice.com/
OBJECT_REGISTRY: Dict[str, FreshserviceObjectType] = {
    "tickets": FreshserviceObjectType(
        path="tickets",
        plural_key="tickets",
        singular_key="ticket",
        supports_updated_since=True,
        filter_style="filter_endpoint",
        includes=[
            "conversations",
            "requester",
            "requested_for",
            "stats",
            "problem",
            "assets",
            "changes",
            "related_tickets",
            "onboarding_context",
            "offboarding_context",
        ],
        sub_resources=[
            "conversations",
            "tasks",
            "time_entries",
            "activities",
            "approvals",
        ],
        write_sub_resources=["notes", "reply", "tasks", "time_entries"],
        notes=(
            "Status: 2=Open, 3=Pending, 4=Resolved, 5=Closed. Priority: 1=Low, 2=Medium, 3=High, 4=Urgent. "
            "Listing returns only tickets created in the last 30 days unless updated_since is set."
        ),
    ),
    "problems": FreshserviceObjectType(
        path="problems",
        plural_key="problems",
        singular_key="problem",
        supports_updated_since=True,
        sub_resources=["tasks", "time_entries", "notes"],
        write_sub_resources=["notes", "tasks", "time_entries"],
        notes="Status: 1=Open, 2=Change Requested, 3=Closed. Priority: 1=Low, 2=Medium, 3=High, 4=Urgent.",
    ),
    "changes": FreshserviceObjectType(
        path="changes",
        plural_key="changes",
        singular_key="change",
        supports_updated_since=True,
        filter_style="query_param",
        sub_resources=["tasks", "time_entries", "notes", "approvals"],
        write_sub_resources=["notes", "tasks", "time_entries"],
        notes="Status: 1=Open, 2=Planning, 3=Approval, 4=Pending Release, 5=Pending Review, 6=Closed. Search example: status:1 AND priority:4",
    ),
    "releases": FreshserviceObjectType(
        path="releases",
        plural_key="releases",
        singular_key="release",
        supports_updated_since=True,
        sub_resources=["tasks", "time_entries", "notes"],
        write_sub_resources=["notes", "tasks", "time_entries"],
        notes=(
            "Status: 1=Open, 2=On hold, 3=In Progress, 4=Incomplete, 5=Completed. "
            "Predefined filters via additional_query_params='filter_name=my_open' (all, my_open, unassigned, completed, incompleted, deleted)."
        ),
    ),
    "requesters": FreshserviceObjectType(
        path="requesters",
        plural_key="requesters",
        singular_key="requester",
        filter_style="query_param",
        notes=(
            "End users who raise tickets. Search example: primary_email:'jane@example.com'. "
            "Deleting a requester deactivates it (it is not permanently removed)."
        ),
    ),
    "agents": FreshserviceObjectType(
        path="agents",
        plural_key="agents",
        singular_key="agent",
        filter_style="query_param",
        notes=(
            "Support staff. Search example: email:'ops@example.com'. "
            "Deleting an agent deactivates it (it is not permanently removed)."
        ),
    ),
    "agent_groups": FreshserviceObjectType(
        path="groups",
        plural_key="groups",
        singular_key="group",
    ),
    "requester_groups": FreshserviceObjectType(
        path="requester_groups",
        plural_key="requester_groups",
        singular_key="requester_group",
        sub_resources=["members"],
    ),
    "departments": FreshserviceObjectType(
        path="departments",
        plural_key="departments",
        singular_key="department",
        filter_style="query_param",
        notes="Search example: name:'Engineering'",
    ),
    "locations": FreshserviceObjectType(
        path="locations",
        plural_key="locations",
        singular_key="location",
        filter_style="query_param",
    ),
    "products": FreshserviceObjectType(
        path="products",
        plural_key="products",
        singular_key="product",
    ),
    "vendors": FreshserviceObjectType(
        path="vendors",
        plural_key="vendors",
        singular_key="vendor",
    ),
    "assets": FreshserviceObjectType(
        path="assets",
        plural_key="assets",
        singular_key="asset",
        filter_style="filter_param",
        includes=["type_fields"],
        sub_resources=["components", "contracts", "requests", "relationships"],
        notes="Assets are addressed by display_id (not id). Search example: asset_state:'IN USE' AND asset_type_id:123",
    ),
    "asset_types": FreshserviceObjectType(
        path="asset_types",
        plural_key="asset_types",
        singular_key="asset_type",
    ),
    "software": FreshserviceObjectType(
        path="applications",
        plural_key="application",  # the API uses a singular key for the list response
        singular_key="application",
        sub_resources=["licenses", "installations"],
    ),
    "contracts": FreshserviceObjectType(
        path="contracts",
        plural_key="contracts",
        singular_key="contract",
    ),
    "purchase_orders": FreshserviceObjectType(
        path="purchase_orders",
        plural_key="purchase_orders",
        singular_key="purchase_order",
    ),
    "service_categories": FreshserviceObjectType(
        path="service_catalog/categories",
        plural_key="service_categories",
        singular_key="service_category",
        writable=False,
    ),
    "service_catalog_items": FreshserviceObjectType(
        path="service_catalog/items",
        plural_key="service_items",
        singular_key="service_item",
        writable=False,
        notes="Service catalog items are addressed by display_id. Read-only via the API.",
    ),
    "solution_categories": FreshserviceObjectType(
        path="solutions/categories",
        plural_key="categories",
        singular_key="category",
    ),
    "solution_folders": FreshserviceObjectType(
        path="solutions/folders",
        plural_key="folders",
        singular_key="folder",
        notes="Listing requires additional_query_params='category_id=<id>' (get a category id from solution_categories first).",
    ),
    "solution_articles": FreshserviceObjectType(
        path="solutions/articles",
        plural_key="articles",
        singular_key="article",
        notes="Listing requires additional_query_params='folder_id=<id>' (get a folder id from solution_folders first).",
    ),
    "sla_policies": FreshserviceObjectType(
        path="sla_policies",
        plural_key="sla_policies",
        singular_key="sla_policy",
        writable=False,
    ),
    "business_hours": FreshserviceObjectType(
        path="business_hours",
        plural_key="business_hours",
        singular_key="business_hours",
        writable=False,
    ),
    "announcements": FreshserviceObjectType(
        path="announcements",
        plural_key="announcements",
        singular_key="announcement",
    ),
    "ticket_fields": FreshserviceObjectType(
        path="ticket_form_fields",
        plural_key="ticket_fields",
        singular_key="ticket_field",
        writable=False,
        notes="Field definitions for tickets, including custom fields and allowed values.",
    ),
    "roles": FreshserviceObjectType(
        path="roles",
        plural_key="roles",
        singular_key="role",
        writable=False,
    ),
    "workspaces": FreshserviceObjectType(
        path="workspaces",
        plural_key="workspaces",
        singular_key="workspace",
        writable=False,
    ),
    "custom_objects": FreshserviceObjectType(
        path="objects",
        plural_key="custom_objects",
        singular_key="custom_object",
        sub_resources=["records"],
        writable=False,
        write_sub_resources=["records"],
        notes=(
            "User-defined objects. Object definitions are read-only, but their records support "
            "full CRUD via the 'records' sub-resource; record pagination uses additional "
            "non-standard parameters (page_size, next_page_link)."
        ),
    ),
}

VALID_OBJECT_TYPES = ", ".join(sorted(OBJECT_REGISTRY.keys()))

SEARCHABLE_OBJECT_TYPES = ", ".join(
    sorted(name for name, spec in OBJECT_REGISTRY.items() if spec.filter_style)
)

SUB_RESOURCE_OBJECT_TYPES = ", ".join(
    sorted(name for name, spec in OBJECT_REGISTRY.items() if spec.sub_resources)
)

WRITABLE_OBJECT_TYPES = ", ".join(
    sorted(name for name, spec in OBJECT_REGISTRY.items() if spec.writable)
)

WRITE_SUB_RESOURCE_OBJECT_TYPES = ", ".join(
    sorted(name for name, spec in OBJECT_REGISTRY.items() if spec.write_sub_resources)
)


class FreshserviceConfig(ToolsetConfig):
    """Configuration for the Freshservice API (Freshworks).

    Example configuration:
    ```yaml
    api_url: "https://your-domain.freshservice.com"
    api_key: "{{ env.FRESHSERVICE_API_KEY }}"
    ```
    """

    api_url: str = Field(
        title="API URL",
        description="Freshservice instance base URL",
        examples=["https://your-domain.freshservice.com"],
    )
    api_key: str = Field(
        title="API Key",
        description="Freshservice API key (found under Profile Settings in the Freshservice UI)",
        examples=["{{ env.FRESHSERVICE_API_KEY }}"],
    )
    default_page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        title="Default Page Size",
        description=f"Default number of records returned by list/search tools when the LLM does not specify per_page (max {MAX_PAGE_SIZE})",
    )
    timeout_seconds: int = Field(
        default=30,
        title="Request Timeout",
        description="Timeout in seconds for Freshservice API requests",
    )
    health_check_object: str = Field(
        default="tickets",
        title="Health check object type",
        description="Object type listed on startup to verify connectivity and permissions. Change this if your API key cannot access the default object type.",
        examples=["tickets", "agents", "departments"],
    )
    enable_write_tools: bool = Field(
        default=False,
        title="Enable Write Tools",
        description=(
            "Expose tools that create, update and delete Freshservice objects. "
            "When false (default), only read tools are available."
        ),
    )
    require_approval_for_writes: bool = Field(
        default=True,
        title="Require Approval For Writes",
        description=(
            "When write tools are enabled, require human approval before each "
            "create/update/delete call. Set to false for fully autonomous writes."
        ),
    )


class FreshserviceToolset(Toolset):
    config_classes: ClassVar[List[Type[BaseModel]]] = [FreshserviceConfig]

    def __init__(self):
        super().__init__(
            name="freshservice",
            description=(
                "Access to Freshservice (Freshworks ITSM): tickets, problems, changes, releases, "
                "assets, requesters, agents and every other Freshservice object. Read-only by "
                "default; create/update/delete tools can be enabled via config."
            ),
            icon_url="https://upload.wikimedia.org/wikipedia/commons/2/2f/Freshworks-vector-logo.svg",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/freshservice/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=self._build_tools(include_write_tools=True),
        )

        self._reload_instructions()

    def _build_tools(self, include_write_tools: bool) -> List[Tool]:
        tools: List[Tool] = [
            ListObjectTypes(self),
            ListObjects(self),
            GetObject(self),
            SearchObjects(self),
            ListRelatedObjects(self),
        ]
        if include_write_tools:
            tools.extend(
                [
                    CreateObject(self),
                    UpdateObject(self),
                    DeleteObject(self),
                    CreateRelatedObject(self),
                    UpdateRelatedObject(self),
                    DeleteRelatedObject(self),
                ]
            )
        return tools

    def _reload_instructions(self):
        """Load Freshservice specific instructions for the LLM."""
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        """Validate the Freshservice configuration and check API connectivity."""
        if not config:
            return False, TOOLSET_CONFIG_MISSING_ERROR

        try:
            self.config = FreshserviceConfig(**config)
        except Exception as e:
            return False, f"Failed to validate Freshservice configuration: {str(e)}"

        # Only expose write tools when explicitly enabled; rebuild so a config
        # reload can both add and remove them.
        self.tools = self._build_tools(
            include_write_tools=self.fs_config.enable_write_tools
        )
        self._reload_instructions()

        return self._perform_health_check()

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Perform a live health check by listing one record of the configured object type."""
        object_type = self.fs_config.health_check_object
        spec = OBJECT_REGISTRY.get(object_type)
        if not spec:
            return (
                False,
                f"Invalid health_check_object '{object_type}'. Valid object types: {VALID_OBJECT_TYPES}",
            )

        try:
            self._make_api_request(
                endpoint=f"api/v2/{spec.path}",
                query_params={"per_page": 1},
                timeout=min(self.fs_config.timeout_seconds, 10),
            )
            return (
                True,
                f"Freshservice configuration is valid and API is accessible at {self.fs_config.api_url} (checked object type: {object_type})",
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:500] if e.response is not None else ""
            if status_code == 401:
                return (
                    False,
                    f"Freshservice authentication failed. Please check your API key. Full error: {status_code} - {body}",
                )
            elif status_code == 403:
                return (
                    False,
                    f"Freshservice access denied for object type '{object_type}'. Configure 'health_check_object' to an object type your API key can access. Full error: {status_code} - {body}",
                )
            return (
                False,
                f"Freshservice API returned an error: {status_code} - {body}",
            )
        except requests.exceptions.ConnectionError as e:
            return (
                False,
                f"Failed to connect to Freshservice at {self.fs_config.api_url}. Full error: {str(e)}",
            )
        except requests.exceptions.Timeout:
            return False, "Freshservice health check timed out"
        except Exception as e:
            return False, f"Freshservice health check failed: {str(e)}"

    @property
    def fs_config(self) -> FreshserviceConfig:
        return cast(FreshserviceConfig, self.config)

    def _make_api_request(
        self,
        endpoint: str,
        query_params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Make a GET request to the Freshservice API.

        Args:
            endpoint: API endpoint path relative to the instance URL (e.g., "api/v2/tickets")
            query_params: Optional query parameters
            timeout: Request timeout in seconds (defaults to the configured timeout)

        Returns:
            Tuple of (parsed JSON response, response headers)

        Raises:
            requests.exceptions.RequestException subclasses on failure
        """
        url = urljoin(self.fs_config.api_url.rstrip("/") + "/", endpoint.lstrip("/"))
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            # Freshservice uses HTTP basic auth with the API key as the
            # username and any string as the password.
            auth=HTTPBasicAuth(self.fs_config.api_key, "X"),
            params=query_params,
            timeout=timeout or self.fs_config.timeout_seconds,
        )
        response.raise_for_status()
        return response.json(), dict(response.headers)

    def _make_write_api_request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """Make a POST/PUT/DELETE request to the Freshservice API.

        Returns:
            Tuple of (parsed JSON response or empty dict for bodyless responses,
            HTTP status code)

        Raises:
            requests.exceptions.RequestException subclasses on failure
        """
        url = urljoin(self.fs_config.api_url.rstrip("/") + "/", endpoint.lstrip("/"))
        response = requests.request(
            method,
            url,
            headers={"Accept": "application/json"},
            auth=HTTPBasicAuth(self.fs_config.api_key, "X"),
            json=payload,
            timeout=self.fs_config.timeout_seconds,
        )
        response.raise_for_status()
        # DELETE typically returns 204 with no body
        if response.status_code == 204 or not response.content:
            return {}, response.status_code
        return response.json(), response.status_code


def _resolve_updated_since(value: Any) -> str:
    """Convert an updated_since value into RFC3339.

    Accepts RFC3339 strings as-is, positive integers as unix timestamps, and
    negative integers as seconds relative to now.
    """
    if is_int(value):
        int_value = int(value)
        if int_value < 0:
            return unix_to_rfc3339(int(time.time()) + int_value)
        return unix_to_rfc3339(int_value)
    if isinstance(value, str) and is_rfc3339(value):
        return value
    raise ValueError(
        f"Invalid updated_since value '{value}'. Use an RFC3339 timestamp (e.g. 2025-01-31T00:00:00Z) or a negative integer of seconds relative to now (e.g. -86400 for the last 24 hours)."
    )


def _parse_additional_query_params(raw: str) -> Dict[str, str]:
    parsed = dict(parse_qsl(raw, keep_blank_values=True))
    if not parsed:
        raise ValueError(
            f"Invalid additional_query_params '{raw}'. Expected URL query string format, e.g. 'category_id=123' or 'type=incident&email=a@b.com'."
        )
    return parsed


class BaseFreshserviceTool(Tool, ABC):
    """Base class for Freshservice tools with shared request/error handling."""

    def __init__(self, toolset: FreshserviceToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _get_object_spec(
        self, params: dict
    ) -> Tuple[Optional[FreshserviceObjectType], Optional[StructuredToolResult]]:
        object_type = params.get("object_type", "")
        spec = OBJECT_REGISTRY.get(object_type)
        if not spec:
            return None, StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unknown object_type '{object_type}'. Valid object types: {VALID_OBJECT_TYPES}",
                params=params,
            )
        return spec, None

    def _ensure_configured(self, params: dict) -> Optional[StructuredToolResult]:
        if not isinstance(self._toolset.config, FreshserviceConfig):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )
        return None

    def _resolve_pagination(self, params: dict, query_params: Dict[str, Any]) -> None:
        per_page = params.get("per_page") or self._toolset.fs_config.default_page_size
        query_params["per_page"] = min(int(per_page), MAX_PAGE_SIZE)
        if params.get("page"):
            query_params["page"] = int(params["page"])

    def _fetch(
        self,
        endpoint: str,
        query_params: Dict[str, Any],
        params: dict,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> StructuredToolResult:
        """Run the request and translate the response/errors into a StructuredToolResult."""
        request_description = f"GET /{endpoint} with query parameters {json.dumps(query_params, default=str)}"
        try:
            data, headers = self._toolset._make_api_request(
                endpoint=endpoint, query_params=query_params
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:1000] if e.response is not None else ""
            hints = []
            if status_code == 403:
                hints.append(
                    "The API key lacks permission for this object type, or the Freshservice plan does not include it."
                )
            if status_code == 429:
                retry_after = (
                    e.response.headers.get("Retry-After")
                    if e.response is not None
                    else None
                )
                hints.append(
                    f"Rate limit exceeded. Retry after {retry_after or 'a few'} seconds."
                )
            hint_text = (" " + " ".join(hints)) if hints else ""
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Freshservice request failed with HTTP {status_code}.{hint_text}\n"
                    f"Request: {request_description}\n"
                    f"Response body: {body}"
                ),
                params=params,
            )
        except requests.exceptions.RequestException as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to reach Freshservice.\nRequest: {request_description}\nError: {str(e)}",
                params=params,
            )

        response_data: Dict[str, Any] = dict(extra_data or {})
        if isinstance(data, dict):
            response_data.update(data)
        else:
            response_data["result"] = data

        # Freshservice signals more pages via the Link header (rel="next").
        link_header = headers.get("Link") or headers.get("link")
        if link_header:
            response_data["_pagination"] = {
                "has_more_pages": 'rel="next"' in link_header,
                "hint": "Fetch the next page by incrementing the 'page' parameter.",
            }

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=response_data,
            params=params,
        )


class ListObjectTypes(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_list_object_types",
            description=(
                "Lists all Freshservice object types that the other freshservice_* tools can operate on, "
                "including their search support, valid 'include' values, available sub-resources (relations) "
                "and usage notes. Call this first when unsure which object_type to use."
            ),
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        catalog = {
            name: {
                "searchable": bool(spec.filter_style),
                "supports_updated_since": spec.supports_updated_since,
                "include_values": spec.includes,
                "sub_resources": spec.sub_resources,
                "notes": spec.notes,
            }
            for name, spec in OBJECT_REGISTRY.items()
        }
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={"object_types": catalog},
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List available object types"


class ListObjects(BaseFreshserviceTool, JsonFilterMixin):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_list_objects",
            description=(
                "Lists Freshservice objects of a given type (GET /api/v2/{object_path}). "
                f"Valid object types: {VALID_OBJECT_TYPES}. "
                "Results are paginated; the response contains a '_pagination' entry when more pages exist. "
                "For searching by field values, prefer freshservice_search_objects when the object type supports it."
            ),
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "object_type": ToolParameter(
                        description=f"The type of Freshservice object to list. One of: {VALID_OBJECT_TYPES}",
                        type="string",
                        required=True,
                    ),
                    "page": ToolParameter(
                        description="Page number to fetch (default: 1)",
                        type="integer",
                        required=False,
                    ),
                    "per_page": ToolParameter(
                        description=f"Number of records per page (default: {DEFAULT_PAGE_SIZE}, max: {MAX_PAGE_SIZE})",
                        type="integer",
                        required=False,
                    ),
                    "updated_since": ToolParameter(
                        description=(
                            "Only return records updated at or after this time. RFC3339 timestamp "
                            "(e.g. 2025-01-31T00:00:00Z) or a negative integer of seconds relative to now "
                            "(e.g. -86400 for the last 24 hours). Only supported by tickets, problems, changes and releases."
                        ),
                        type="string",
                        required=False,
                    ),
                    "include": ToolParameter(
                        description=(
                            "Comma-separated list of related data to embed in each record. "
                            "Valid values depend on the object type (see freshservice_list_object_types). "
                            "Example for tickets: 'stats'"
                        ),
                        type="string",
                        required=False,
                    ),
                    "additional_query_params": ToolParameter(
                        description=(
                            "Extra query parameters in URL query string format, for object-type specific filters. "
                            "Examples: 'category_id=123' (solution_folders), 'folder_id=456' (solution_articles), "
                            "'email=jane@example.com' (requesters), 'type=incident' (tickets)."
                        ),
                        type="string",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]

        query_params: Dict[str, Any] = {}
        self._resolve_pagination(params, query_params)

        if params.get("updated_since"):
            if not spec.supports_updated_since:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=(
                        f"Object type '{params['object_type']}' does not support the updated_since parameter. "
                        "Remove it and filter the results client-side (e.g. with the jq parameter) instead."
                    ),
                    params=params,
                )
            try:
                query_params["updated_since"] = _resolve_updated_since(
                    params["updated_since"]
                )
            except ValueError as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=str(e),
                    params=params,
                )

        if params.get("include"):
            query_params["include"] = params["include"]

        if params.get("additional_query_params"):
            try:
                query_params.update(
                    _parse_additional_query_params(params["additional_query_params"])
                )
            except ValueError as e:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    error=str(e),
                    params=params,
                )

        result = self._fetch(
            endpoint=f"api/v2/{spec.path}", query_params=query_params, params=params
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List {object_type}"


class GetObject(BaseFreshserviceTool, JsonFilterMixin):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_get_object",
            description=(
                "Retrieves a single Freshservice object by its ID (GET /api/v2/{object_path}/{id}). "
                f"Valid object types: {VALID_OBJECT_TYPES}. "
                "The ID MUST come from the user or from a previous freshservice_list_objects/freshservice_search_objects "
                "response - never guess IDs. For assets and service_catalog_items, use the display_id field."
            ),
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "object_type": ToolParameter(
                        description=f"The type of Freshservice object to fetch. One of: {VALID_OBJECT_TYPES}",
                        type="string",
                        required=True,
                    ),
                    "object_id": ToolParameter(
                        description=(
                            "The object's numeric ID from a real Freshservice record (the 'id' field, "
                            "or 'display_id' for assets and service_catalog_items). Never fabricate this value."
                        ),
                        type="integer",
                        required=True,
                    ),
                    "include": ToolParameter(
                        description=(
                            "Comma-separated list of related data to embed. Valid values depend on the object type "
                            "(see freshservice_list_object_types). Example for tickets: 'conversations,stats'"
                        ),
                        type="string",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]

        query_params: Dict[str, Any] = {}
        if params.get("include"):
            query_params["include"] = params["include"]

        result = self._fetch(
            endpoint=f"api/v2/{spec.path}/{params['object_id']}",
            query_params=query_params,
            params=params,
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        object_id = params.get("object_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get {object_type} {object_id}"


class SearchObjects(BaseFreshserviceTool, JsonFilterMixin):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_search_objects",
            description=(
                "Searches Freshservice objects with a server-side filter query. "
                f"Supported object types: {SEARCHABLE_OBJECT_TYPES}. "
                "Query syntax: field:value conditions combined with AND/OR, e.g. "
                '"status:2 AND priority:4", "agent_id:5001 OR group_id:21", '
                "\"created_at:>'2025-01-01'\", \"asset_state:'IN USE'\". "
                "Quote string values in single quotes. Use freshservice_list_objects for unfiltered listing."
            ),
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "object_type": ToolParameter(
                        description=f"The type of Freshservice object to search. One of: {SEARCHABLE_OBJECT_TYPES}",
                        type="string",
                        required=True,
                    ),
                    "query": ToolParameter(
                        description=(
                            "The filter query WITHOUT surrounding double quotes (they are added automatically). "
                            "Example: status:2 AND priority:4"
                        ),
                        type="string",
                        required=True,
                    ),
                    "page": ToolParameter(
                        description="Page number to fetch (default: 1)",
                        type="integer",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]

        if not spec.filter_style:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Object type '{params['object_type']}' does not support server-side search. "
                    f"Searchable object types: {SEARCHABLE_OBJECT_TYPES}. "
                    "Use freshservice_list_objects and filter client-side (jq parameter) instead."
                ),
                params=params,
            )

        query = params["query"].strip()
        # The API requires the query value to be wrapped in double quotes.
        if not (query.startswith('"') and query.endswith('"')):
            query = f'"{query}"'

        query_params: Dict[str, Any] = {}
        if params.get("page"):
            query_params["page"] = int(params["page"])

        if spec.filter_style == "filter_endpoint":
            endpoint = f"api/v2/{spec.path}/filter"
            query_params["query"] = query
        elif spec.filter_style == "filter_param":
            endpoint = f"api/v2/{spec.path}"
            query_params["filter"] = query
        else:  # query_param
            endpoint = f"api/v2/{spec.path}"
            query_params["query"] = query

        result = self._fetch(
            endpoint=endpoint, query_params=query_params, params=params
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        query = params.get("query", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search {object_type} matching {query}"


class ListRelatedObjects(BaseFreshserviceTool, JsonFilterMixin):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_list_related_objects",
            description=(
                "Lists sub-resources of a Freshservice object (GET /api/v2/{object_path}/{id}/{relation}), "
                "e.g. the conversations, tasks or time_entries of a ticket, or the notes of a problem/change. "
                f"Object types with sub-resources: {SUB_RESOURCE_OBJECT_TYPES}. "
                "Check freshservice_list_object_types for the relations available per object type."
            ),
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "object_type": ToolParameter(
                        description=f"The type of the parent Freshservice object. One of: {SUB_RESOURCE_OBJECT_TYPES}",
                        type="string",
                        required=True,
                    ),
                    "object_id": ToolParameter(
                        description=(
                            "The parent object's numeric ID from a real Freshservice record "
                            "(display_id for assets). Never fabricate this value."
                        ),
                        type="integer",
                        required=True,
                    ),
                    "relation": ToolParameter(
                        description="The sub-resource to list, e.g. 'conversations', 'tasks', 'time_entries', 'notes'",
                        type="string",
                        required=True,
                    ),
                    "page": ToolParameter(
                        description="Page number to fetch (default: 1)",
                        type="integer",
                        required=False,
                    ),
                    "per_page": ToolParameter(
                        description=f"Number of records per page (default: {DEFAULT_PAGE_SIZE}, max: {MAX_PAGE_SIZE})",
                        type="integer",
                        required=False,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]

        relation = params["relation"]
        if relation not in spec.sub_resources:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Object type '{params['object_type']}' has no '{relation}' sub-resource. "
                    f"Available sub-resources: {', '.join(spec.sub_resources) or 'none'}."
                ),
                params=params,
            )

        query_params: Dict[str, Any] = {}
        self._resolve_pagination(params, query_params)

        result = self._fetch(
            endpoint=f"api/v2/{spec.path}/{params['object_id']}/{relation}",
            query_params=query_params,
            params=params,
        )
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        object_id = params.get("object_id", "")
        relation = params.get("relation", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List {relation} of {object_type} {object_id}"


class BaseFreshserviceWriteTool(BaseFreshserviceTool, ABC):
    """Base class for tools that create, update or delete Freshservice objects.

    Write tools are only exposed when `enable_write_tools` is true, and by
    default each call requires human approval (`require_approval_for_writes`).
    """

    def __init__(self, toolset: FreshserviceToolset, *args, **kwargs):
        super().__init__(toolset, *args, **kwargs)

    def requires_approval(
        self, params: Dict, context: ToolInvokeContext
    ) -> Optional[ApprovalRequirement]:
        config = self._toolset.config
        if (
            isinstance(config, FreshserviceConfig)
            and not config.require_approval_for_writes
        ):
            return None
        return ApprovalRequirement(
            needs_approval=True,
            reason=(
                f"'{self.name}' modifies data in Freshservice. "
                "Set require_approval_for_writes: false in the toolset config to skip this approval."
            ),
        )

    def _parse_object_data(
        self, params: dict
    ) -> Tuple[Optional[Dict[str, Any]], Optional[StructuredToolResult]]:
        raw = params.get("object_data")
        if not raw:
            return None, StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error='object_data is required and must be a JSON object string, e.g. \'{"subject": "...", "priority": 2}\'',
                params=params,
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"object_data is not valid JSON: {e}. Received: {raw[:500]}",
                params=params,
            )
        if not isinstance(data, dict):
            return None, StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"object_data must be a JSON object (dict), got {type(data).__name__}",
                params=params,
            )
        return data, None

    def _check_writable(
        self, spec: FreshserviceObjectType, params: dict
    ) -> Optional[StructuredToolResult]:
        if not spec.writable:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Object type '{params['object_type']}' is read-only in the Freshservice API. "
                    f"Writable object types: {WRITABLE_OBJECT_TYPES}."
                ),
                params=params,
            )
        return None

    def _write(
        self,
        method: str,
        endpoint: str,
        params: dict,
        payload: Optional[Dict[str, Any]] = None,
    ) -> StructuredToolResult:
        request_description = f"{method} /{endpoint}"
        if payload is not None:
            request_description += (
                f" with body {json.dumps(payload, default=str)[:1000]}"
            )
        try:
            data, status_code = self._toolset._make_write_api_request(
                method=method, endpoint=endpoint, payload=payload
            )
        except requests.exceptions.HTTPError as e:
            http_status = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:1000] if e.response is not None else ""
            hints = []
            if http_status == 403:
                hints.append(
                    "The API key lacks permission for this operation, or the Freshservice plan does not include it."
                )
            if http_status == 400:
                hints.append(
                    "The request body failed validation; check the 'errors' array in the response for the offending fields."
                )
            if http_status == 405:
                hints.append("This endpoint does not support this operation.")
            hint_text = (" " + " ".join(hints)) if hints else ""
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Freshservice write request failed with HTTP {http_status}.{hint_text}\n"
                    f"Request: {request_description}\n"
                    f"Response body: {body}"
                ),
                params=params,
            )
        except requests.exceptions.RequestException as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to reach Freshservice.\nRequest: {request_description}\nError: {str(e)}",
                params=params,
            )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={"http_status": status_code, **data},
            params=params,
        )


class CreateObject(BaseFreshserviceWriteTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_create_object",
            description=(
                "Creates a Freshservice object (POST /api/v2/{object_path}). "
                f"Writable object types: {WRITABLE_OBJECT_TYPES}. "
                "Requires the object fields as a JSON string. Check freshservice_list_object_types "
                "for status/priority codes, and use real IDs for reference fields (requester_id, group_id, ...)."
            ),
            parameters={
                "object_type": ToolParameter(
                    description=f"The type of Freshservice object to create. One of: {WRITABLE_OBJECT_TYPES}",
                    type="string",
                    required=True,
                ),
                "object_data": ToolParameter(
                    description=(
                        "The object fields as a JSON object string. Example for tickets: "
                        '\'{"subject": "DB connection errors", "description": "...", "email": "reporter@example.com", '
                        '"status": 2, "priority": 3}\''
                    ),
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]
        error = self._check_writable(spec, params)
        if error:
            return error
        payload, error = self._parse_object_data(params)
        if error:
            return error

        return self._write(
            method="POST",
            endpoint=f"api/v2/{spec.path}",
            params=params,
            payload=payload,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Create {object_type} object"


class UpdateObject(BaseFreshserviceWriteTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_update_object",
            description=(
                "Updates fields of an existing Freshservice object (PUT /api/v2/{object_path}/{id}). "
                f"Writable object types: {WRITABLE_OBJECT_TYPES}. "
                "Only include the fields to change in object_data. The ID MUST come from the user or a "
                "previous freshservice_list_objects/freshservice_search_objects response - never guess IDs."
            ),
            parameters={
                "object_type": ToolParameter(
                    description=f"The type of Freshservice object to update. One of: {WRITABLE_OBJECT_TYPES}",
                    type="string",
                    required=True,
                ),
                "object_id": ToolParameter(
                    description=(
                        "The object's numeric ID from a real Freshservice record (the 'id' field, "
                        "or 'display_id' for assets). Never fabricate this value."
                    ),
                    type="integer",
                    required=True,
                ),
                "object_data": ToolParameter(
                    description=(
                        "The fields to update as a JSON object string. Example: "
                        '\'{"status": 4, "priority": 1}\''
                    ),
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]
        error = self._check_writable(spec, params)
        if error:
            return error
        payload, error = self._parse_object_data(params)
        if error:
            return error

        return self._write(
            method="PUT",
            endpoint=f"api/v2/{spec.path}/{params['object_id']}",
            params=params,
            payload=payload,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        object_id = params.get("object_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Update {object_type} {object_id}"


class DeleteObject(BaseFreshserviceWriteTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_delete_object",
            description=(
                "Deletes a Freshservice object (DELETE /api/v2/{object_path}/{id}). "
                f"Writable object types: {WRITABLE_OBJECT_TYPES}. "
                "Deleting tickets moves them to trash; deleting requesters/agents deactivates them. "
                "The ID MUST come from the user or a previous tool response - never guess IDs."
            ),
            parameters={
                "object_type": ToolParameter(
                    description=f"The type of Freshservice object to delete. One of: {WRITABLE_OBJECT_TYPES}",
                    type="string",
                    required=True,
                ),
                "object_id": ToolParameter(
                    description=(
                        "The object's numeric ID from a real Freshservice record (the 'id' field, "
                        "or 'display_id' for assets). Never fabricate this value."
                    ),
                    type="integer",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]
        error = self._check_writable(spec, params)
        if error:
            return error

        return self._write(
            method="DELETE",
            endpoint=f"api/v2/{spec.path}/{params['object_id']}",
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        object_id = params.get("object_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Delete {object_type} {object_id}"


class CreateRelatedObject(BaseFreshserviceWriteTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_create_related_object",
            description=(
                "Creates a sub-resource on a Freshservice object (POST /api/v2/{object_path}/{id}/{relation}), "
                "e.g. add a note or reply to a ticket, a task or time entry to a ticket/problem/change/release, "
                "or a record to a custom object. "
                f"Object types with writable sub-resources: {WRITE_SUB_RESOURCE_OBJECT_TYPES}. "
                "Check freshservice_list_object_types for the writable relations per object type."
            ),
            parameters={
                "object_type": ToolParameter(
                    description=f"The type of the parent Freshservice object. One of: {WRITE_SUB_RESOURCE_OBJECT_TYPES}",
                    type="string",
                    required=True,
                ),
                "object_id": ToolParameter(
                    description="The parent object's numeric ID from a real Freshservice record. Never fabricate this value.",
                    type="integer",
                    required=True,
                ),
                "relation": ToolParameter(
                    description="The sub-resource to create, e.g. 'notes', 'reply', 'tasks', 'time_entries', 'records'",
                    type="string",
                    required=True,
                ),
                "object_data": ToolParameter(
                    description=(
                        "The sub-resource fields as a JSON object string. Example for a ticket note: "
                        '\'{"body": "Investigation findings: ...", "private": true}\''
                    ),
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]

        relation = params["relation"]
        if relation not in spec.write_sub_resources:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Object type '{params['object_type']}' has no writable '{relation}' sub-resource. "
                    f"Writable sub-resources: {', '.join(spec.write_sub_resources) or 'none'}."
                ),
                params=params,
            )

        payload, error = self._parse_object_data(params)
        if error:
            return error

        return self._write(
            method="POST",
            endpoint=f"api/v2/{spec.path}/{params['object_id']}/{relation}",
            params=params,
            payload=payload,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        object_id = params.get("object_id", "")
        relation = params.get("relation", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Add {relation} to {object_type} {object_id}"


class UpdateRelatedObject(BaseFreshserviceWriteTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_update_related_object",
            description=(
                "Updates a sub-resource of a Freshservice object "
                "(PUT /api/v2/{object_path}/{id}/{relation}/{related_id}), e.g. edit a task, "
                "time entry or note of a ticket/problem/change/release, or a custom object record. "
                f"Object types with writable sub-resources: {WRITE_SUB_RESOURCE_OBJECT_TYPES}."
            ),
            parameters={
                "object_type": ToolParameter(
                    description=f"The type of the parent Freshservice object. One of: {WRITE_SUB_RESOURCE_OBJECT_TYPES}",
                    type="string",
                    required=True,
                ),
                "object_id": ToolParameter(
                    description="The parent object's numeric ID from a real Freshservice record. Never fabricate this value.",
                    type="integer",
                    required=True,
                ),
                "relation": ToolParameter(
                    description="The sub-resource type, e.g. 'tasks', 'time_entries', 'notes', 'records'",
                    type="string",
                    required=True,
                ),
                "related_object_id": ToolParameter(
                    description="The sub-resource's ID from a real Freshservice record. Never fabricate this value.",
                    type="integer",
                    required=True,
                ),
                "object_data": ToolParameter(
                    description="The fields to update as a JSON object string.",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]

        relation = params["relation"]
        if relation not in spec.write_sub_resources:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Object type '{params['object_type']}' has no writable '{relation}' sub-resource. "
                    f"Writable sub-resources: {', '.join(spec.write_sub_resources) or 'none'}."
                ),
                params=params,
            )

        payload, error = self._parse_object_data(params)
        if error:
            return error

        return self._write(
            method="PUT",
            endpoint=f"api/v2/{spec.path}/{params['object_id']}/{relation}/{params['related_object_id']}",
            params=params,
            payload=payload,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        object_id = params.get("object_id", "")
        relation = params.get("relation", "")
        related_id = params.get("related_object_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Update {relation} {related_id} of {object_type} {object_id}"


class DeleteRelatedObject(BaseFreshserviceWriteTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_delete_related_object",
            description=(
                "Deletes a sub-resource of a Freshservice object "
                "(DELETE /api/v2/{object_path}/{id}/{relation}/{related_id}), e.g. remove a task, "
                "time entry or note of a ticket/problem/change/release, or a custom object record. "
                f"Object types with writable sub-resources: {WRITE_SUB_RESOURCE_OBJECT_TYPES}."
            ),
            parameters={
                "object_type": ToolParameter(
                    description=f"The type of the parent Freshservice object. One of: {WRITE_SUB_RESOURCE_OBJECT_TYPES}",
                    type="string",
                    required=True,
                ),
                "object_id": ToolParameter(
                    description="The parent object's numeric ID from a real Freshservice record. Never fabricate this value.",
                    type="integer",
                    required=True,
                ),
                "relation": ToolParameter(
                    description="The sub-resource type, e.g. 'tasks', 'time_entries', 'notes', 'records'",
                    type="string",
                    required=True,
                ),
                "related_object_id": ToolParameter(
                    description="The sub-resource's ID from a real Freshservice record. Never fabricate this value.",
                    type="integer",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        error = self._ensure_configured(params)
        if error:
            return error
        spec, error = self._get_object_spec(params)
        if error or not spec:
            return error  # type: ignore[return-value]

        relation = params["relation"]
        if relation not in spec.write_sub_resources:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Object type '{params['object_type']}' has no writable '{relation}' sub-resource. "
                    f"Writable sub-resources: {', '.join(spec.write_sub_resources) or 'none'}."
                ),
                params=params,
            )

        return self._write(
            method="DELETE",
            endpoint=f"api/v2/{spec.path}/{params['object_id']}/{relation}/{params['related_object_id']}",
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        object_type = params.get("object_type", "unknown")
        object_id = params.get("object_id", "")
        relation = params.get("relation", "")
        related_id = params.get("related_object_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Delete {relation} {related_id} of {object_type} {object_id}"
