import json
import logging
import os
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type
from urllib.parse import quote

import requests  # type: ignore[import-untyped]
from pydantic import ConfigDict, Field, model_validator
from requests.auth import HTTPBasicAuth

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
from holmes.plugins.toolsets.consts import (
    STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
    TOOLSET_CONFIG_MISSING_ERROR,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    DEFAULT_LOG_LIMIT,
    DEFAULT_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.utils import (
    process_timestamps_to_rfc3339,
    standard_start_datetime_tool_param_description,
    toolset_name_for_one_liner,
)
from holmes.utils.pydantic_utils import ToolsetConfig


class VictoriaLogsConfig(ToolsetConfig):
    """Configuration for VictoriaLogs API access.

    Example configuration (no auth):
    ```yaml
    api_url: "http://victorialogs.monitoring.svc:9428"
    ```

    With basic auth:
    ```yaml
    api_url: "https://victorialogs.example.com"
    username: "your-username"
    password: "your-password"
    ```

    With bearer token:
    ```yaml
    api_url: "https://victorialogs.example.com"
    bearer_token: "your-token"
    ```
    """

    api_url: str = Field(
        title="API URL",
        description="Base URL of the VictoriaLogs server (without trailing slash).",
        examples=["http://victorialogs.monitoring.svc:9428"],
    )
    username: Optional[str] = Field(
        default=None,
        title="Username",
        description="Username for basic auth (optional).",
    )
    password: Optional[str] = Field(
        default=None,
        title="Password",
        description="Password for basic auth (optional).",
    )
    bearer_token: Optional[str] = Field(
        default=None,
        title="Bearer Token",
        description="Bearer token for Authorization header (optional). Mutually exclusive with basic auth.",
    )
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        title="Additional Headers",
        description="Additional HTTP headers to send with each request, e.g. tenant headers (AccountID/ProjectID).",
        examples=[{"AccountID": "0", "ProjectID": "0"}],
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates.",
    )
    timeout_seconds: int = Field(
        default=30,
        title="Timeout Seconds",
        description="Request timeout in seconds.",
    )
    external_url: Optional[str] = Field(
        default=None,
        title="External URL",
        description=(
            "Optional public URL of VictoriaLogs (or vmui) used to build clickable links. "
            "Defaults to api_url when not set."
        ),
    )

    @model_validator(mode="after")
    def validate_auth(self) -> "VictoriaLogsConfig":
        if self.bearer_token and (self.username or self.password):
            raise ValueError(
                "authentication method must be either bearer_token or basic auth, not both"
            )
        if self.username and not self.password:
            raise ValueError("password is required when username is set")
        if self.password and not self.username:
            raise ValueError("username is required when password is set")
        return self


def _build_explore_url(
    config: VictoriaLogsConfig,
    query: str,
    start: Optional[str],
    end: Optional[str],
) -> Optional[str]:
    """Build a clickable VMUI link. VMUI uses hash-based routing."""
    try:
        base_url = (config.external_url or config.api_url).rstrip("/")
        encoded_query = quote(query, safe="")
        params = [f"query={encoded_query}"]
        if start:
            params.append(f"start={quote(start, safe='')}")
        if end:
            params.append(f"end={quote(end, safe='')}")
        return f"{base_url}/select/vmui/#/?{'&'.join(params)}"
    except Exception:
        return None


class VictoriaLogsToolset(Toolset):
    """Toolset for querying logs from VictoriaLogs using LogsQL."""

    config_classes: ClassVar[list[Type[VictoriaLogsConfig]]] = [VictoriaLogsConfig]
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self):
        super().__init__(
            name="victorialogs",
            enabled=False,
            description=(
                "Query logs from VictoriaLogs using LogsQL. "
                "Provides search, log stream discovery, and field/value enumeration."
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/victorialogs/",
            icon_url="https://avatars.githubusercontent.com/u/43783956?s=200&v=4",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        self.tools = [
            VictoriaLogsQuery(self),
            VictoriaLogsStreams(self),
            VictoriaLogsFieldNames(self),
            VictoriaLogsFieldValues(self),
            VictoriaLogsHits(self),
        ]
        self._reload_instructions()

    def _reload_instructions(self) -> None:
        instructions_filepath = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "instructions.jinja2")
        )
        self._load_llm_instructions(jinja_template=f"file://{instructions_filepath}")

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, TOOLSET_CONFIG_MISSING_ERROR
        try:
            self.config = VictoriaLogsConfig(**config)
        except Exception as e:
            return False, f"Failed to validate VictoriaLogs configuration: {e}"
        return self._perform_health_check()

    @property
    def victorialogs_config(self) -> VictoriaLogsConfig:
        return self.config  # type: ignore

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Connect to /health endpoint to verify the server is reachable."""
        cfg = self.victorialogs_config
        url = f"{cfg.api_url.rstrip('/')}/health"
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                auth=self._get_auth(),
                timeout=min(cfg.timeout_seconds, 10),
                verify=cfg.verify_ssl,
            )
            response.raise_for_status()
            text = response.text.strip()
            if text.upper() != "OK":
                # Health endpoint returned 200 but not "OK" - still likely fine
                logging.debug(
                    "VictoriaLogs health check returned unexpected body: %r", text
                )
            return True, f"Connected to VictoriaLogs at {cfg.api_url}"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = e.response.text[:300] if e.response is not None else ""
            return False, f"VictoriaLogs health check failed: HTTP {status} - {body}"
        except requests.exceptions.SSLError as e:
            return False, f"VictoriaLogs SSL error: {e}"
        except requests.exceptions.ConnectionError as e:
            return False, f"Failed to connect to VictoriaLogs at {cfg.api_url}: {e}"
        except requests.exceptions.Timeout:
            return False, "VictoriaLogs health check timed out"
        except Exception as e:
            return False, f"VictoriaLogs health check failed: {e}"

    def _get_headers(self) -> Dict[str, str]:
        cfg = self.victorialogs_config
        headers: Dict[str, str] = {"Accept": "application/json"}
        if cfg.bearer_token:
            headers["Authorization"] = f"Bearer {cfg.bearer_token}"
        if cfg.headers:
            headers.update(cfg.headers)
        return headers

    def _get_auth(self) -> Optional[HTTPBasicAuth]:
        cfg = self.victorialogs_config
        if cfg.username and cfg.password:
            return HTTPBasicAuth(cfg.username, cfg.password)
        return None

    def _post(
        self, endpoint: str, data: Dict[str, Any], stream: bool = False
    ) -> requests.Response:
        """POST form-encoded request to a VictoriaLogs API endpoint."""
        cfg = self.victorialogs_config
        url = f"{cfg.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        # Filter out None values so they aren't sent as the literal string "None"
        body = {k: v for k, v in data.items() if v is not None and v != ""}
        response = requests.post(
            url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            data=body,
            timeout=cfg.timeout_seconds,
            verify=cfg.verify_ssl,
            stream=stream,
        )
        response.raise_for_status()
        return response


class _BaseVictoriaLogsTool(Tool, ABC):
    """Base class for VictoriaLogs tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: VictoriaLogsToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    def _ensure_configured(
        self, params: dict
    ) -> Optional[StructuredToolResult]:
        if self._toolset.config is None:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=TOOLSET_CONFIG_MISSING_ERROR,
                params=params,
            )
        return None

    def _http_error_result(
        self,
        e: requests.exceptions.HTTPError,
        endpoint: str,
        request_data: Dict[str, Any],
        params: dict,
    ) -> StructuredToolResult:
        status = e.response.status_code if e.response is not None else "?"
        body = e.response.text[:1000] if e.response is not None else ""
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=(
                f"VictoriaLogs request to {endpoint} failed with HTTP {status}.\n"
                f"Request parameters: {json.dumps(request_data, default=str)}\n"
                f"Response body: {body}"
            ),
            params=params,
        )

    def _connection_error_result(
        self,
        e: Exception,
        endpoint: str,
        request_data: Dict[str, Any],
        params: dict,
    ) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=(
                f"Failed to reach VictoriaLogs at endpoint {endpoint}: {e}.\n"
                f"Request parameters: {json.dumps(request_data, default=str)}"
            ),
            params=params,
        )


def _parse_jsonl(text: str) -> List[Dict[str, Any]]:
    """Parse VictoriaLogs JSON Lines responses."""
    results: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            # Skip malformed lines but keep going
            logging.debug("Skipping non-JSON line from VictoriaLogs: %r", line[:200])
    return results


def _resolve_time_range(
    start: Optional[str], end: Optional[str]
) -> Tuple[str, str]:
    return process_timestamps_to_rfc3339(
        start_timestamp=start,
        end_timestamp=end,
        default_time_span_seconds=DEFAULT_TIME_SPAN_SECONDS,
    )


class VictoriaLogsQuery(_BaseVictoriaLogsTool):
    """Run a LogsQL query and return matching log entries."""

    def __init__(self, toolset: VictoriaLogsToolset):
        super().__init__(
            toolset=toolset,
            name="victorialogs_query",
            description=(
                "Search VictoriaLogs using a LogsQL query. Returns matching log entries "
                "as a list of JSON objects. Always include the most specific filters "
                "you can (stream selectors like {namespace=\"foo\"}, word/phrase filters, "
                "field filters such as level:error)."
            ),
            parameters={
                "query": ToolParameter(
                    description=(
                        "LogsQL query string. Examples:\n"
                        " - 'error _time:1h'\n"
                        " - '{namespace=\"app-1\",service=\"checkout\"} level:error'\n"
                        " - '{app=\"nginx\"} ~\"5\\\\d\\\\d\"'\n"
                        "If you only want a basic match-everything query, use '*'."
                    ),
                    type="string",
                    required=True,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description=(
                        f"Maximum number of log entries to return (default: {DEFAULT_LOG_LIMIT}). "
                        "Has no effect for queries that include their own '| limit' pipe."
                    ),
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        early = self._ensure_configured(params)
        if early:
            return early

        start, end = _resolve_time_range(params.get("start"), params.get("end"))
        limit = params.get("limit") or DEFAULT_LOG_LIMIT
        query = params.get("query") or "*"

        request_data = {
            "query": query,
            "start": start,
            "end": end,
            "limit": limit,
        }
        endpoint = "/select/logsql/query"
        url = _build_explore_url(self._toolset.victorialogs_config, query, start, end)
        try:
            response = self._toolset._post(endpoint, request_data)
            entries = _parse_jsonl(response.text)
            if not entries:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=(
                        f"No logs returned for query '{query}' between {start} and {end} "
                        f"(limit={limit})."
                    ),
                    params=params,
                    url=url,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=entries,
                params=params,
                url=url,
            )
        except requests.exceptions.HTTPError as e:
            return self._http_error_result(e, endpoint, request_data, params)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ) as e:
            return self._connection_error_result(e, endpoint, request_data, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        query = params.get("query", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: query {query}"


class VictoriaLogsStreams(_BaseVictoriaLogsTool):
    """List log streams matching a LogsQL query."""

    def __init__(self, toolset: VictoriaLogsToolset):
        super().__init__(
            toolset=toolset,
            name="victorialogs_streams",
            description=(
                "List log streams matching a LogsQL query. Each stream is identified by its "
                "stream label set, e.g. {namespace=\"app-1\",service=\"checkout\"}. "
                "Useful for discovering which apps/namespaces have logs in a time window."
            ),
            parameters={
                "query": ToolParameter(
                    description=(
                        "LogsQL query (default '*' to list all streams in the time range)."
                    ),
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of streams to return (default: 100).",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        early = self._ensure_configured(params)
        if early:
            return early

        start, end = _resolve_time_range(params.get("start"), params.get("end"))
        query = params.get("query") or "*"
        limit = params.get("limit") or 100
        request_data = {"query": query, "start": start, "end": end, "limit": limit}
        endpoint = "/select/logsql/streams"
        try:
            response = self._toolset._post(endpoint, request_data)
            data = response.json()
            values = data.get("values", []) if isinstance(data, dict) else []
            if not values:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=(
                        f"No streams found for query '{query}' between {start} and {end}."
                    ),
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=values,
                params=params,
            )
        except requests.exceptions.HTTPError as e:
            return self._http_error_result(e, endpoint, request_data, params)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ) as e:
            return self._connection_error_result(e, endpoint, request_data, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: streams "
            f"{params.get('query', '*')}"
        )


class VictoriaLogsFieldNames(_BaseVictoriaLogsTool):
    """List field names present in logs matching a query."""

    def __init__(self, toolset: VictoriaLogsToolset):
        super().__init__(
            toolset=toolset,
            name="victorialogs_field_names",
            description=(
                "List field names present in logs matching a LogsQL query within the time range. "
                "Useful before composing a query to discover which fields exist (e.g. 'level', 'service')."
            ),
            parameters={
                "query": ToolParameter(
                    description="LogsQL query (default '*' to list fields across all logs).",
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        early = self._ensure_configured(params)
        if early:
            return early

        start, end = _resolve_time_range(params.get("start"), params.get("end"))
        query = params.get("query") or "*"
        request_data = {"query": query, "start": start, "end": end}
        endpoint = "/select/logsql/field_names"
        try:
            response = self._toolset._post(endpoint, request_data)
            data = response.json()
            values = data.get("values", []) if isinstance(data, dict) else []
            if not values:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=(
                        f"No field names found for query '{query}' between {start} and {end}."
                    ),
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=values,
                params=params,
            )
        except requests.exceptions.HTTPError as e:
            return self._http_error_result(e, endpoint, request_data, params)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ) as e:
            return self._connection_error_result(e, endpoint, request_data, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: field names "
            f"{params.get('query', '*')}"
        )


class VictoriaLogsFieldValues(_BaseVictoriaLogsTool):
    """List values of a given field in logs matching a query."""

    def __init__(self, toolset: VictoriaLogsToolset):
        super().__init__(
            toolset=toolset,
            name="victorialogs_field_values",
            description=(
                "List unique values of a given field in logs matching a LogsQL query, "
                "with hit counts. Useful for discovering valid label values (e.g. all "
                "distinct namespaces or service names)."
            ),
            parameters={
                "field": ToolParameter(
                    description="Name of the field to enumerate (e.g. 'level', 'namespace').",
                    type="string",
                    required=True,
                ),
                "query": ToolParameter(
                    description="LogsQL query (default '*').",
                    type="string",
                    required=False,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of values to return (default: 100).",
                    type="integer",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        early = self._ensure_configured(params)
        if early:
            return early

        field = params.get("field")
        if not field:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter 'field'",
                params=params,
            )

        start, end = _resolve_time_range(params.get("start"), params.get("end"))
        query = params.get("query") or "*"
        limit = params.get("limit") or 100
        request_data = {
            "query": query,
            "field": field,
            "start": start,
            "end": end,
            "limit": limit,
        }
        endpoint = "/select/logsql/field_values"
        try:
            response = self._toolset._post(endpoint, request_data)
            data = response.json()
            values = data.get("values", []) if isinstance(data, dict) else []
            if not values:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.NO_DATA,
                    data=(
                        f"No values found for field '{field}' (query='{query}') "
                        f"between {start} and {end}."
                    ),
                    params=params,
                )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=values,
                params=params,
            )
        except requests.exceptions.HTTPError as e:
            return self._http_error_result(e, endpoint, request_data, params)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ) as e:
            return self._connection_error_result(e, endpoint, request_data, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: values of "
            f"{params.get('field', '?')}"
        )


class VictoriaLogsHits(_BaseVictoriaLogsTool):
    """Compute log counts grouped by time bucket for a query."""

    def __init__(self, toolset: VictoriaLogsToolset):
        super().__init__(
            toolset=toolset,
            name="victorialogs_hits",
            description=(
                "Return time-bucketed log counts for a LogsQL query. "
                "Useful for spotting bursts of errors or traffic patterns."
            ),
            parameters={
                "query": ToolParameter(
                    description="LogsQL query (e.g. 'level:error').",
                    type="string",
                    required=True,
                ),
                "start": ToolParameter(
                    description=standard_start_datetime_tool_param_description(
                        DEFAULT_TIME_SPAN_SECONDS
                    ),
                    type="string",
                    required=False,
                ),
                "end": ToolParameter(
                    description=STANDARD_END_DATETIME_TOOL_PARAM_DESCRIPTION,
                    type="string",
                    required=False,
                ),
                "step": ToolParameter(
                    description=(
                        "Bucket size as a duration string (e.g. '1m', '5m', '1h'). "
                        "Default: '5m'."
                    ),
                    type="string",
                    required=False,
                ),
                "field": ToolParameter(
                    description=(
                        "Optional field to group results by (e.g. 'level'). "
                        "When set, returns one series per distinct value."
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        early = self._ensure_configured(params)
        if early:
            return early

        query = params.get("query")
        if not query:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing required parameter 'query'",
                params=params,
            )

        start, end = _resolve_time_range(params.get("start"), params.get("end"))
        step = params.get("step") or "5m"
        request_data: Dict[str, Any] = {
            "query": query,
            "start": start,
            "end": end,
            "step": step,
        }
        if params.get("field"):
            request_data["field"] = params["field"]

        endpoint = "/select/logsql/hits"
        try:
            response = self._toolset._post(endpoint, request_data)
            data = response.json()
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except requests.exceptions.HTTPError as e:
            return self._http_error_result(e, endpoint, request_data, params)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ) as e:
            return self._connection_error_result(e, endpoint, request_data, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: hits "
            f"{params.get('query', '')}"
        )
