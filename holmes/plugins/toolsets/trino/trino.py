import os
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type, cast

import requests
from pydantic import Field

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
    ToolParameter,
)
from holmes.plugins.toolsets.http_api_base import (
    HTTPAPITool,
    HTTPAPIToolset,
    HTTPAPIToolsetConfig,
)
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class TrinoConfig(HTTPAPIToolsetConfig):
    trino_user: str = Field(
        default="holmes", description="Trino user sent in X-Trino-User"
    )
    catalog: Optional[str] = Field(default=None, description="Default Trino catalog")
    schema_name: Optional[str] = Field(
        default=None, alias="schema", description="Default Trino schema"
    )
    source: str = Field(default="holmesgpt", description="Trino client source")
    max_rows: int = Field(default=200, ge=1, le=2000)
    max_pages: int = Field(default=20, ge=1, le=100)


class TrinoToolset(HTTPAPIToolset):
    config_classes: ClassVar[List[Type[TrinoConfig]]] = [TrinoConfig]

    def __init__(self) -> None:
        super().__init__(
            name="trino",
            description="Inspect Trino and execute bounded read-only SQL queries",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/trino/",
            icon_url="https://trino.io/assets/trino-og.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
        )
        self.tools = [TrinoClusterInfo(self), TrinoQuery(self)]
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

    @property
    def trino_config(self) -> TrinoConfig:
        return cast(TrinoConfig, self.config)

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        return self._health_check(TrinoConfig, config, "/v1/info")

    def _trino_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "X-Trino-User": self.trino_config.trino_user,
            "X-Trino-Source": self.trino_config.source,
        }
        if self.trino_config.catalog:
            headers["X-Trino-Catalog"] = self.trino_config.catalog
        if self.trino_config.schema_name:
            headers["X-Trino-Schema"] = self.trino_config.schema_name
        return headers


class TrinoClusterInfo(HTTPAPITool):
    def __init__(self, toolset: TrinoToolset):
        super().__init__(
            toolset,
            name="trino_cluster_info",
            description=(
                "Return Trino coordinator version, environment, uptime, and node state."
            ),
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return self._get_json("/v1/info", params, context)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: cluster info"


class TrinoQuery(HTTPAPITool):
    _READ_ONLY_PREFIX: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|VALUES|WITH)\b", re.IGNORECASE
    )
    _MUTATING_KEYWORD: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(ALTER|CALL|CREATE|DELETE|DROP|GRANT|INSERT|MERGE|RENAME|REVOKE|"
        r"SET|TRUNCATE|UPDATE|USE)\b",
        re.IGNORECASE,
    )

    def __init__(self, toolset: TrinoToolset):
        super().__init__(
            toolset,
            name="trino_query",
            description=(
                "Execute one bounded read-only Trino SQL statement. Only SELECT, SHOW, "
                "DESCRIBE, EXPLAIN, VALUES, and non-mutating WITH queries are allowed."
            ),
            parameters={
                "query": ToolParameter(
                    type="string", required=True, description="Read-only Trino SQL"
                )
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        query = str(params.get("query", "")).strip()
        if not query or not self._READ_ONLY_PREFIX.match(query):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Trino query rejected: only read-only SQL is allowed.",
                params=params,
            )
        without_trailing_semicolon = query[:-1] if query.endswith(";") else query
        if ";" in without_trailing_semicolon or self._MUTATING_KEYWORD.search(query):
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    "Trino query rejected: multiple or mutating statements "
                    "are not allowed."
                ),
                params=params,
            )

        rows: List[Any] = []
        columns: List[Dict[str, Any]] = []
        pages = 0
        next_uri: Optional[str] = None
        last_payload: Dict[str, Any] = {}
        try:
            response = self._toolset._request(
                "POST",
                "/v1/statement",
                data=query,
                request_context=context.request_context,
                headers=cast(TrinoToolset, self._toolset)._trino_headers(),
            )
            while True:
                pages += 1
                last_payload = response.json()
                if last_payload.get("error"):
                    return StructuredToolResult(
                        status=StructuredToolResultStatus.ERROR,
                        error=(
                            f"Trino query failed. Query: {query}. "
                            f"Error: {last_payload['error']}"
                        ),
                        params=params,
                        url=last_payload.get("infoUri"),
                    )
                if last_payload.get("columns"):
                    columns = last_payload["columns"]
                rows.extend(last_payload.get("data") or [])
                next_uri = last_payload.get("nextUri")
                if (
                    not next_uri
                    or pages >= cast(TrinoToolset, self._toolset).trino_config.max_pages
                    or len(rows)
                    >= cast(TrinoToolset, self._toolset).trino_config.max_rows
                ):
                    break
                response = self._toolset._request(
                    "GET", next_uri, request_context=context.request_context
                )

            truncated = bool(next_uri)
            if truncated:
                try:
                    self._toolset._request(
                        "DELETE", next_uri, request_context=context.request_context
                    )
                except requests.exceptions.RequestException:
                    pass
            max_rows = cast(TrinoToolset, self._toolset).trino_config.max_rows
            data = {
                "query_id": last_payload.get("id"),
                "columns": columns,
                "rows": rows[:max_rows],
                "row_count": min(len(rows), max_rows),
                "truncated": truncated or len(rows) > max_rows,
                "stats": last_payload.get("stats"),
            }
            status = (
                StructuredToolResultStatus.SUCCESS
                if rows or columns
                else StructuredToolResultStatus.NO_DATA
            )
            return StructuredToolResult(
                status=status,
                data=data,
                params=params,
                url=last_payload.get("infoUri"),
            )
        except requests.exceptions.HTTPError as error:
            status = (
                error.response.status_code
                if error.response is not None
                else "unknown"
            )
            body = error.response.text if error.response is not None else ""
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"Trino query failed with HTTP {status}. Query: {query}. "
                    f"Response body: {body}"
                ),
                params=params,
            )
        except requests.exceptions.RequestException as error:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to reach Trino while executing query '{query}': {error}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: "
            f"{params.get('query', '')}"
        )
