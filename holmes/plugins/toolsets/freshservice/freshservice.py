import json
import os
from abc import ABC
from typing import Any, ClassVar, Dict, NamedTuple, Optional, Tuple, Type, cast
from urllib.parse import parse_qsl

import requests  # type: ignore
from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    ToolParameter,
    Toolset,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 30


class FreshserviceObjectType(NamedTuple):
    """Maps a logical object type to its Freshservice API v2 endpoint."""

    path: str  # endpoint path under /api/v2/
    list_key: str  # key wrapping records in list responses
    item_key: str  # key wrapping the record in single-record responses
    writable: bool = True  # whether create/update are supported by the API
    # The newer ITAM API (/api/v2/itam/*, Device42-based) requires a trailing
    # slash on create/update/delete URLs; POST without it silently behaves as
    # a list call instead of a create.
    trailing_slash: bool = False


# Object types exposed by the generic list/get/create/update tools.
# Note: some modules (e.g. vendors, products) are plan-dependent and
# the API returns 403 require_feature when unavailable - that error is
# passed through to the LLM.
OBJECT_TYPES: Dict[str, FreshserviceObjectType] = {
    "tickets": FreshserviceObjectType("tickets", "tickets", "ticket"),
    "problems": FreshserviceObjectType("problems", "problems", "problem"),
    "changes": FreshserviceObjectType("changes", "changes", "change"),
    "releases": FreshserviceObjectType("releases", "releases", "release"),
    "requesters": FreshserviceObjectType("requesters", "requesters", "requester"),
    "agents": FreshserviceObjectType("agents", "agents", "agent"),
    "groups": FreshserviceObjectType("groups", "groups", "group"),
    "departments": FreshserviceObjectType("departments", "departments", "department"),
    "locations": FreshserviceObjectType("locations", "locations", "location"),
    "vendors": FreshserviceObjectType("vendors", "vendors", "vendor"),
    "products": FreshserviceObjectType("products", "products", "product"),
    "assets": FreshserviceObjectType(
        "itam/assets", "assets", "asset", trailing_slash=True
    ),
    "devices": FreshserviceObjectType(
        "itam/devices", "devices", "device", trailing_slash=True
    ),
    "software": FreshserviceObjectType("applications", "applications", "application"),
    "contracts": FreshserviceObjectType("contracts", "contracts", "contract"),
    "purchase_orders": FreshserviceObjectType(
        "purchase_orders", "purchase_orders", "purchase_order"
    ),
    "solution_categories": FreshserviceObjectType(
        "solutions/categories", "categories", "category"
    ),
    "solution_folders": FreshserviceObjectType(
        "solutions/folders", "folders", "folder"
    ),
    "solution_articles": FreshserviceObjectType(
        "solutions/articles", "articles", "article"
    ),
    "service_catalog_items": FreshserviceObjectType(
        "service_catalog/items", "service_items", "service_item", writable=False
    ),
}

# Object types that support the POST /{path}/{id}/notes endpoint
NOTE_OBJECT_TYPES = ("tickets", "problems", "changes", "releases")


class FreshserviceConfig(ToolsetConfig):
    """Configuration for the Freshservice (Freshworks) toolset.

    Example configuration:
    ```yaml
    api_url: "https://yourdomain.freshservice.com"
    api_key: "your-api-key"
    ```
    """

    api_url: str = Field(
        title="API URL",
        description="Freshservice instance base URL",
        examples=["https://yourdomain.freshservice.com"],
    )
    api_key: str = Field(
        title="API Key",
        description="Freshservice API key (found under Profile Settings)",
    )
    readonly: bool = Field(
        default=False,
        title="Read-only mode",
        description="When true, tools that create or modify records are disabled",
    )
    default_page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        title="Default page size",
        description="Default number of records returned by list tools",
    )


class FreshserviceToolset(Toolset):
    config_classes: ClassVar[list[Type[FreshserviceConfig]]] = [FreshserviceConfig]

    def __init__(self):
        super().__init__(
            name="freshservice",
            description="Read and write Freshservice (Freshworks) ITSM data: tickets, problems, changes, releases, assets, requesters, knowledge base and more",
            icon_url="https://raw.githubusercontent.com/robusta-dev/holmesgpt/master/images/integration_logos/freshservice-icon.png",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/freshservice/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                FreshserviceListRecords(self),
                FreshserviceGetRecord(self),
                FreshserviceFilterTickets(self),
                FreshserviceGetTicketConversations(self),
                FreshserviceSearchSolutionArticles(self),
                FreshserviceCreateRecord(self),
                FreshserviceUpdateRecord(self),
                FreshserviceAddNote(self),
            ],
        )
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = FreshserviceConfig(**config)
        except Exception as e:
            return False, f"Failed to validate Freshservice configuration: {str(e)}"
        return self._perform_health_check()

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Verify connectivity and credentials with a minimal API call."""
        try:
            response = self._request(
                "GET", "tickets", query_params={"per_page": 1}, timeout=10
            )
            if response.status_code == 401:
                return (
                    False,
                    f"Freshservice authentication failed, check your API key. Full error: {response.status_code} - {response.text}",
                )
            if response.status_code == 403:
                return (
                    False,
                    f"Freshservice access denied, ensure your API key belongs to an agent with ticket access. Full error: {response.status_code} - {response.text}",
                )
            if not response.ok:
                return (
                    False,
                    f"Freshservice API returned an error: {response.status_code} - {response.text}",
                )
            return True, "Freshservice configuration is valid and the API is accessible"
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

    def _request(
        self,
        method: str,
        path: str,
        query_params: Optional[Dict[str, Any]] = None,
        payload: Optional[dict] = None,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> requests.Response:
        """Perform an authenticated request against the Freshservice API v2."""
        url = f"{self.fs_config.api_url.rstrip('/')}/api/v2/{path.lstrip('/')}"
        return requests.request(
            method,
            url,
            auth=(self.fs_config.api_key, "X"),
            headers={"Content-Type": "application/json"},
            params=query_params,
            json=payload,
            timeout=timeout,
        )


class BaseFreshserviceTool(Tool, ABC):
    def __init__(self, toolset: FreshserviceToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _call_api(
        self,
        method: str,
        path: str,
        params: dict,
        query_params: Optional[Dict[str, Any]] = None,
        payload: Optional[dict] = None,
    ) -> StructuredToolResult:
        """Call the API and wrap the response in a StructuredToolResult.

        Errors include the method, URL, parameters and the full API response so
        the LLM can self-correct (e.g. fix an invalid field or filter).
        """
        response = self._toolset._request(
            method, path, query_params=query_params, payload=payload
        )
        if not response.ok:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Freshservice API error: {method} {response.request.url} "
                    f"returned {response.status_code}: {response.text}"
                ),
                params=params,
            )
        data = response.json() if response.text else {}
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
        )

    def _object_type(self, params: dict) -> FreshserviceObjectType:
        object_type = params.get("object_type", "")
        if object_type not in OBJECT_TYPES:
            raise ValueError(
                f"Unknown object_type '{object_type}'. Valid values: {', '.join(sorted(OBJECT_TYPES))}"
            )
        return OBJECT_TYPES[object_type]

    def _reject_if_readonly(self, params: dict) -> Optional[StructuredToolResult]:
        if self._toolset.fs_config.readonly:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="This Freshservice toolset is configured as read-only (readonly: true); create/update operations are disabled",
                params=params,
            )
        return None


OBJECT_TYPE_PARAM = ToolParameter(
    description=f"The type of Freshservice record. One of: {', '.join(sorted(OBJECT_TYPES))}",
    type="string",
    required=True,
    enum=sorted(OBJECT_TYPES),
)


class FreshserviceListRecords(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_list_records",
            description=(
                "List Freshservice records of a given type using GET /api/v2/{type}. "
                "Results are paginated. Listing solution_articles requires "
                "query_params='folder_id=<id>' (list solution_folders first)."
            ),
            parameters={
                "object_type": OBJECT_TYPE_PARAM,
                "page": ToolParameter(
                    description="Page number to fetch (default: 1)",
                    type="integer",
                    required=False,
                ),
                "per_page": ToolParameter(
                    description=f"Records per page (default: {DEFAULT_PAGE_SIZE}, max: {MAX_PAGE_SIZE})",
                    type="integer",
                    required=False,
                ),
                "query_params": ToolParameter(
                    description=(
                        "Optional additional query parameters as a URL query string, e.g. "
                        "'updated_since=2026-07-01T00:00:00Z' or 'email=user@example.com' for tickets/requesters, "
                        "'filter=new_and_my_open' for tickets, 'folder_id=123' for solution_articles. "
                        "Only parameters supported by the Freshservice API for that record type are accepted."
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        obj = self._object_type(params)
        query_params: Dict[str, Any] = dict(
            parse_qsl(params.get("query_params") or "")
        )
        query_params["page"] = params.get("page") or 1
        query_params["per_page"] = min(
            int(params.get("per_page") or self._toolset.fs_config.default_page_size),
            MAX_PAGE_SIZE,
        )
        return self._call_api("GET", obj.path, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List {params.get('object_type', 'records')}"


class FreshserviceGetRecord(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_get_record",
            description=(
                "Get a single Freshservice record by ID using GET /api/v2/{type}/{id}. "
                "The ID must come from a previous list/filter tool response or from the user - never guess IDs. "
                "For assets use the asset_id field, for devices the device_id field."
            ),
            parameters={
                "object_type": OBJECT_TYPE_PARAM,
                "record_id": ToolParameter(
                    description="The record ID (asset_id for assets, device_id for devices)",
                    type="integer",
                    required=True,
                ),
                "include": ToolParameter(
                    description=(
                        "Optional comma-separated related data to embed. "
                        "For tickets: conversations, requester, stats, problem, assets, change_initiating_ticket, change_initiated_by_ticket. "
                        "For changes: onhold_since, initiating_tickets. Only valid values for the record type are accepted."
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        obj = self._object_type(params)
        query_params = {}
        if params.get("include"):
            query_params["include"] = params["include"]
        return self._call_api(
            "GET",
            f"{obj.path}/{params['record_id']}",
            params,
            query_params=query_params or None,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: "
            f"Get {params.get('object_type', 'record')} {params.get('record_id', '')}"
        )


class FreshserviceFilterTickets(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_filter_tickets",
            description=(
                "Search tickets with the Freshservice filter query language using GET /api/v2/tickets/filter. "
                "Supports field comparisons combined with AND/OR, e.g.: "
                "\"priority:4 AND status:2\", \"agent_id:null\", \"created_at:>'2026-07-01'\", "
                "\"tag:'payments'\". Filterable fields include: workspace_id, requester_id, email, agent_id, "
                "group_id, priority, status, impact, urgency, tag, due_by, fr_due_by, created_at, updated_at, category."
            ),
            parameters={
                "query": ToolParameter(
                    description="The filter query, without surrounding double quotes (they are added automatically)",
                    type="string",
                    required=True,
                ),
                "page": ToolParameter(
                    description="Page number to fetch (default: 1, 30 tickets per page)",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        query = params["query"].strip().strip('"')
        query_params = {"query": f'"{query}"', "page": params.get("page") or 1}
        return self._call_api(
            "GET", "tickets/filter", params, query_params=query_params
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Filter tickets: {params.get('query', '')}"


class FreshserviceGetTicketConversations(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_get_ticket_conversations",
            description=(
                "Get all conversations (public replies and private notes from agents) for a ticket "
                "using GET /api/v2/tickets/{id}/conversations. Notes often contain triage details, "
                "log excerpts and escalation context that are not in the ticket description."
            ),
            parameters={
                "ticket_id": ToolParameter(
                    description="The ticket ID",
                    type="integer",
                    required=True,
                ),
                "page": ToolParameter(
                    description="Page number to fetch (default: 1)",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        query_params = {"page": params.get("page") or 1}
        return self._call_api(
            "GET",
            f"tickets/{params['ticket_id']}/conversations",
            params,
            query_params=query_params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get conversations for ticket {params.get('ticket_id', '')}"


class FreshserviceSearchSolutionArticles(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_search_solution_articles",
            description=(
                "Search knowledge base (solution) articles by keyword using "
                "GET /api/v2/solutions/articles/search. Useful for finding runbooks, "
                "troubleshooting guides and documented baselines."
            ),
            parameters={
                "search_term": ToolParameter(
                    description="Keywords to search for in article titles and content",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._call_api(
            "GET",
            "solutions/articles/search",
            params,
            query_params={"search_term": params["search_term"]},
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search KB articles: {params.get('search_term', '')}"


class FreshserviceCreateRecord(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_create_record",
            description=(
                "Create a Freshservice record using POST /api/v2/{type} with a JSON payload. "
                "Example ticket payload: {\"subject\": \"...\", \"description\": \"<p>html</p>\", "
                "\"email\": \"requester@example.com\", \"status\": 2, \"priority\": 2}. "
                "Assets require a \"type\" field (e.g. \"Server\", \"Laptop\"); creating an asset "
                "with an existing name makes a duplicate, so list assets first. "
                "On validation errors the API response lists the offending fields - fix them and retry."
            ),
            parameters={
                "object_type": OBJECT_TYPE_PARAM,
                "data": ToolParameter(
                    description="The record fields as a JSON object string",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        rejected = self._reject_if_readonly(params)
        if rejected:
            return rejected
        obj = self._object_type(params)
        if not obj.writable:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"The Freshservice API does not support creating {params['object_type']}",
                params=params,
            )
        try:
            payload = json.loads(params["data"])
        except json.JSONDecodeError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Invalid JSON in 'data' parameter: {str(e)}",
                params=params,
            )
        path = f"{obj.path}/" if obj.trailing_slash else obj.path
        return self._call_api("POST", path, params, payload=payload)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Create {params.get('object_type', 'record')}"


class FreshserviceUpdateRecord(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_update_record",
            description=(
                "Update fields on an existing Freshservice record using PUT /api/v2/{type}/{id} "
                "with a JSON payload containing only the fields to change, "
                "e.g. {\"status\": 4} to resolve a ticket or {\"group_id\": 123} to reassign it. "
                "Note: changes follow a stateflow - some status transitions are rejected; "
                "the API error explains which transition is invalid."
            ),
            parameters={
                "object_type": OBJECT_TYPE_PARAM,
                "record_id": ToolParameter(
                    description="The ID of the record to update (from a previous tool response, never guessed)",
                    type="integer",
                    required=True,
                ),
                "data": ToolParameter(
                    description="The fields to update as a JSON object string",
                    type="string",
                    required=True,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        rejected = self._reject_if_readonly(params)
        if rejected:
            return rejected
        obj = self._object_type(params)
        if not obj.writable:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"The Freshservice API does not support updating {params['object_type']}",
                params=params,
            )
        try:
            payload = json.loads(params["data"])
        except json.JSONDecodeError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Invalid JSON in 'data' parameter: {str(e)}",
                params=params,
            )
        path = f"{obj.path}/{params['record_id']}"
        if obj.trailing_slash:
            path += "/"
        return self._call_api("PUT", path, params, payload=payload)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: "
            f"Update {params.get('object_type', 'record')} {params.get('record_id', '')}"
        )


class FreshserviceAddNote(BaseFreshserviceTool):
    def __init__(self, toolset: FreshserviceToolset):
        super().__init__(
            toolset=toolset,
            name="freshservice_add_note",
            description=(
                "Add a note to a ticket, problem, change or release using POST /api/v2/{type}/{id}/notes. "
                "Use this to document findings (e.g. a root cause analysis) on the record."
            ),
            parameters={
                "object_type": ToolParameter(
                    description=f"The record type. One of: {', '.join(NOTE_OBJECT_TYPES)}",
                    type="string",
                    required=True,
                    enum=list(NOTE_OBJECT_TYPES),
                ),
                "record_id": ToolParameter(
                    description="The ID of the record to add the note to",
                    type="integer",
                    required=True,
                ),
                "body": ToolParameter(
                    description="The note content (HTML is supported)",
                    type="string",
                    required=True,
                ),
                "private": ToolParameter(
                    description="Tickets only: whether the note is private (visible to agents only). Default: true",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        rejected = self._reject_if_readonly(params)
        if rejected:
            return rejected
        object_type = params.get("object_type", "")
        if object_type not in NOTE_OBJECT_TYPES:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Notes are only supported for: {', '.join(NOTE_OBJECT_TYPES)}",
                params=params,
            )
        payload: Dict[str, Any] = {"body": params["body"]}
        if object_type == "tickets":
            private = params.get("private")
            payload["private"] = True if private is None else bool(private)
        path = f"{OBJECT_TYPES[object_type].path}/{params['record_id']}/notes"
        return self._call_api("POST", path, params, payload=payload)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: "
            f"Add note to {params.get('object_type', 'record')} {params.get('record_id', '')}"
        )
