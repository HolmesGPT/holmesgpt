import os
from abc import ABC
from typing import Any, ClassVar, Dict, Optional, Tuple, Type, cast
from urllib.parse import urljoin

import requests  # type: ignore
from pydantic import BaseModel

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


class ServiceNowTablesConfig(BaseModel):
    """Configuration for ServiceNow Tables API access.

    Example configuration:
    ```yaml
    api_key: "now_1234567890abcdef"
    instance_url: "https://your-instance.service-now.com"
    ```
    """

    api_key: str
    instance_url: str
    api_key_header: str = "x-sn-apikey"


class ServiceNowTablesToolset(Toolset):
    config_class: ClassVar[Type[ServiceNowTablesConfig]] = ServiceNowTablesConfig

    def __init__(self):
        super().__init__(
            name="servicenow/tables",
            description="Tools for retrieving records from ServiceNow tables",
            icon_url="https://www.servicenow.com/content/dam/servicenow-assets/public/en-us/images/og-images/favicon.ico",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/servicenow/",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[
                GetRecords(self),
                GetRecord(self),
            ],
        )

        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        """Check if the ServiceNow configuration is valid and complete."""
        try:
            # Validate the config using Pydantic - this will raise if required fields are missing
            self.config = ServiceNowTablesConfig(**config)

            # Perform health check
            return self._perform_health_check()

        except Exception as e:
            return False, f"Failed to validate ServiceNow configuration: {str(e)}"

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Perform a health check by making a minimal API call."""
        try:
            # Query sys_db_object table with minimal data
            data, headers = self._make_api_request(
                endpoint="api/now/v2/table/sys_db_object",
                query_params={"sysparm_limit": 1, "sysparm_fields": "sys_id"},
                timeout=10,
            )
            return True, "ServiceNow configuration is valid and API is accessible."

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return (
                    False,
                    "ServiceNow authentication failed. Please check your API key.",
                )
            elif e.response.status_code == 403:
                return (
                    False,
                    "ServiceNow access denied. Please ensure your user has Table API access.",
                )
            else:
                return (
                    False,
                    f"ServiceNow API returned error: {e.response.status_code} - {e.response.text}",
                )
        except requests.exceptions.ConnectionError:
            return (
                False,
                f"Failed to connect to ServiceNow instance at {self.config.instance_url if self.config else 'unknown'}",
            )
        except requests.exceptions.Timeout:
            return False, "ServiceNow health check timed out"
        except Exception as e:
            return False, f"ServiceNow health check failed: {str(e)}"

    @property
    def servicenow_config(self) -> ServiceNowTablesConfig:
        return cast(ServiceNowTablesConfig, self.config)

    def get_example_config(self) -> Dict[str, Any]:
        """Return an example configuration for this toolset."""
        example_config = ServiceNowTablesConfig(
            api_key="now_1234567890abcdef",
            instance_url="https://your-instance.service-now.com",
        )
        return example_config.model_dump()

    def _make_api_request(
        self,
        endpoint: str,
        query_params: Optional[Dict] = None,
        timeout: int = 30,
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Make a GET request to ServiceNow API and return JSON data and headers.

        Args:
            endpoint: API endpoint path (e.g., "api/now/v2/table/incident")
            query_params: Optional query parameters for the request
            timeout: Request timeout in seconds

        Returns:
            Tuple of (parsed JSON response data, response headers dict)

        Raises:
            requests.exceptions.HTTPError: For HTTP error responses (4xx, 5xx)
            requests.exceptions.ConnectionError: For connection problems
            requests.exceptions.Timeout: For timeout errors
            requests.exceptions.RequestException: For other request errors
        """
        url = urljoin(
            self.servicenow_config.instance_url.rstrip("/") + "/", endpoint.lstrip("/")
        )

        headers = {
            self.servicenow_config.api_key_header: self.servicenow_config.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        response = requests.get(
            url, headers=headers, params=query_params, timeout=timeout
        )
        response.raise_for_status()
        return response.json(), dict(response.headers)


class BaseServiceNowTool(Tool, ABC):
    """Base class for ServiceNow tools with common HTTP request functionality."""

    def __init__(self, toolset: ServiceNowTablesToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _make_servicenow_request(
        self,
        endpoint: str,
        params: dict,
        query_params: Optional[Dict] = None,
        timeout: int = 30,
    ) -> StructuredToolResult:
        """Make a GET request to ServiceNow API and return structured result.

        Args:
            endpoint: API endpoint path (e.g., "/api/now/v2/table/incident")
            params: Original parameters passed to the tool
            query_params: Optional query parameters for the request
            timeout: Request timeout in seconds

        Returns:
            StructuredToolResult with the API response data
        """
        # TODO: Add URL to the result for better debugging and error messages

        # Use the toolset's shared API request method
        data, headers = self._toolset._make_api_request(
            endpoint=endpoint, query_params=query_params, timeout=timeout
        )

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params=params,
        )


class GetRecords(BaseServiceNowTool):
    def __init__(self, toolset: ServiceNowTablesToolset):
        super().__init__(
            toolset=toolset,
            name="servicenow_get_records",
            description="Retrieves multiple records for the specified table using GET /api/now/v2/table/{tableName}. Returns the records data along with response headers including 'Link' (for pagination) and 'X-Total-Count' (total number of records) if provided by the API.",
            parameters={
                "table_name": ToolParameter(
                    description="The name of the ServiceNow table to query",
                    type="string",
                    required=True,
                ),
                "sysparm_query": ToolParameter(
                    description=(
                        "An encoded query string used to filter the results. "
                        "Use ^ for AND, ^OR for OR. "
                        "Common operators: = (equals), != (not equals), LIKE (contains), "
                        "STARTSWITH, ENDSWITH, CONTAINS, ISNOTEMPTY, ISEMPTY, "
                        "< (less than), <= (less than or equal), > (greater than), >= (greater than or equal). "
                        "Date queries: Use >= and <= operators. Date-only format (YYYY-MM-DD) includes entire day. "
                        "Examples: sys_created_on>=2024-01-01^sys_created_on<=2024-01-31 or with time: sys_created_on>=2024-01-01 00:00:00"
                    ),
                    type="string",
                    required=False,
                ),
                "sysparm_display_value": ToolParameter(
                    description="Return field display values (true), actual values (false), or both (all) (default: true)",
                    type="string",
                    required=False,
                ),
                "sysparm_exclude_reference_link": ToolParameter(
                    description="True to exclude Table API links for reference fields (default: false)",
                    type="boolean",
                    required=False,
                ),
                "sysparm_suppress_pagination_header": ToolParameter(
                    description="Flag that indicates whether to remove the Link header from the response. The Link header provides various URLs to relative pages in the record set which you can use to paginate the returned record set.",
                    type="boolean",
                    required=False,
                ),
                "sysparm_fields": ToolParameter(
                    description="Comma-separated list of fields to return in the response. If not provided, all fields will be returned. Invalid fields are ignored.",
                    type="string",
                    required=False,
                ),
                "sysparm_limit": ToolParameter(
                    description=(
                        "Maximum number of records to return (default: 100). "
                        "For requests that exceed this number of records, use the sysparm_offset parameter to paginate record retrieval. "
                        "This limit is applied before ACL evaluation. If no records return, including records you have access to, "
                        "rearrange the record order so records you have access to return first."
                    ),
                    type="integer",
                    required=False,
                ),
                "sysparm_offset": ToolParameter(
                    description=(
                        "Starting record index for pagination. Use this with sysparm_limit to paginate through large result sets. "
                        "For example, to get records 101-200, use sysparm_offset=100 with sysparm_limit=100."
                    ),
                    type="integer",
                    required=False,
                ),
                "sysparm_view": ToolParameter(
                    description=(
                        "UI view for which to render the data. Determines the fields returned in the response. "
                        "Valid values: desktop, mobile, both. If you also specify the sysparm_fields parameter, it takes precedent. "
                        "In case you are not sure about the fields for sysparm_fields and want to get a short summary, use sysparm_view with 'mobile'."
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """
        Note: The following parameters are available in the ServiceNow API but are not used in this implementation:
        - name-value pairs: Name-value pairs to use to filter the result set. This parameter is mutually exclusive with sysparm_query.
        - sysparm_query_category: Name of the query category (read replica category) to use for queries
        - sysparm_query_no_domain: True to access data across domains if authorized (default: false)
        - sysparm_no_count: Do not execute a select count(*) on table (default: false)
        """
        table_name = params["table_name"]
        query_params = {}

        # Handle sysparm_query
        if params.get("sysparm_query"):
            query_params["sysparm_query"] = params["sysparm_query"]

        # Handle sysparm_display_value with default of 'true' instead of 'false'
        if params.get("sysparm_display_value") is not None:
            query_params["sysparm_display_value"] = params["sysparm_display_value"]
        else:
            query_params["sysparm_display_value"] = "true"

        # Handle other parameters
        if params.get("sysparm_exclude_reference_link") is not None:
            query_params["sysparm_exclude_reference_link"] = str(
                params["sysparm_exclude_reference_link"]
            ).lower()

        if params.get("sysparm_suppress_pagination_header") is not None:
            query_params["sysparm_suppress_pagination_header"] = str(
                params["sysparm_suppress_pagination_header"]
            ).lower()

        if params.get("sysparm_fields"):
            query_params["sysparm_fields"] = params["sysparm_fields"]

        # Handle sysparm_limit with default of 100 instead of 10000
        if params.get("sysparm_limit") is not None:
            query_params["sysparm_limit"] = params["sysparm_limit"]
        else:
            query_params["sysparm_limit"] = 100

        # Handle sysparm_offset for pagination
        if params.get("sysparm_offset") is not None:
            query_params["sysparm_offset"] = params["sysparm_offset"]

        if params.get("sysparm_view"):
            query_params["sysparm_view"] = params["sysparm_view"]

        endpoint = f"/api/now/v2/table/{table_name}"

        # Get data and headers from the API request
        data, headers = self._toolset._make_api_request(
            endpoint=endpoint, query_params=query_params, timeout=30
        )

        # Create the response with records and relevant headers
        response_data = {
            "result": data.get("result", []),
        }

        # Include Link header if present
        if "Link" in headers:
            response_data["Link"] = headers["Link"]

        # Include X-Total-Count header if present
        if "X-Total-Count" in headers:
            response_data["X-Total-Count"] = headers["X-Total-Count"]

        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=response_data,
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        table_name = params.get("table_name", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get records from {table_name}"


class GetRecord(BaseServiceNowTool):
    def __init__(self, toolset: ServiceNowTablesToolset):
        super().__init__(
            toolset=toolset,
            name="servicenow_get_record",
            description="Retrieves the record identified by the specified sys_id from the specified table using GET /api/now/v2/table/{tableName}/{sys_id}",
            parameters={
                "table_name": ToolParameter(
                    description="The name of the ServiceNow table",
                    type="string",
                    required=True,
                ),
                "sys_id": ToolParameter(
                    description="The EXACT sys_id value from a real ServiceNow record. WARNING: You MUST NOT fabricate, guess, or create sys_id values. This value MUST come from: 1) The user providing it explicitly, or 2) A previous servicenow_get_records API response. If you don't have a real sys_id, use servicenow_get_records first to search for records.",
                    type="string",
                    required=True,
                ),
                "sysparm_display_value": ToolParameter(
                    description="Return field display values (true), actual values (false), or both (all) (default: true)",
                    type="string",
                    required=False,
                ),
                "sysparm_exclude_reference_link": ToolParameter(
                    description="True to exclude Table API links for reference fields (default: false)",
                    type="boolean",
                    required=False,
                ),
                "sysparm_fields": ToolParameter(
                    description="Comma-separated list of fields to return in the response. If not provided, all fields will be returned. Invalid fields are ignored.",
                    type="string",
                    required=False,
                ),
                "sysparm_view": ToolParameter(
                    description=(
                        "UI view for which to render the data. Determines the fields returned in the response. "
                        "Valid values: desktop, mobile, both. If you also specify the sysparm_fields parameter, it takes precedent. "
                        "In case you are not sure about the fields for sysparm_fields and want to get a short summary, use sysparm_view with 'mobile'."
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        """
        Note: The following parameter is available in the ServiceNow API but is not used in this implementation:
        - sysparm_query_no_domain: True to access data across domains if authorized (default: false)
        """
        table_name = params["table_name"]
        sys_id = params["sys_id"]
        query_params = {}

        # Handle sysparm_display_value with default of 'true' instead of 'false'
        if params.get("sysparm_display_value") is not None:
            query_params["sysparm_display_value"] = params["sysparm_display_value"]
        else:
            query_params["sysparm_display_value"] = "true"

        # Handle other parameters
        if params.get("sysparm_exclude_reference_link") is not None:
            query_params["sysparm_exclude_reference_link"] = str(
                params["sysparm_exclude_reference_link"]
            ).lower()

        if params.get("sysparm_fields"):
            query_params["sysparm_fields"] = params["sysparm_fields"]

        if params.get("sysparm_view"):
            query_params["sysparm_view"] = params["sysparm_view"]

        endpoint = f"/api/now/v2/table/{table_name}/{sys_id}"
        return self._make_servicenow_request(endpoint, params, query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        table_name = params.get("table_name", "unknown")
        sys_id = params.get("sys_id", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get {table_name} record {sys_id}"
