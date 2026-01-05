import json
from abc import ABC
from typing import Any, ClassVar, Dict, Optional, Tuple, Type

import requests
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
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner


class ElasticsearchConfig(BaseModel):
    """Configuration for Elasticsearch/OpenSearch API access.

    Example configuration:
    ```yaml
    url: "https://your-cluster.es.cloud.io"
    api_key: "base64_encoded_api_key"
    ```

    Or with basic auth:
    ```yaml
    url: "https://your-cluster.es.cloud.io"
    username: "elastic"
    password: "your_password"
    ```
    """

    url: str
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: bool = True
    timeout: int = 10  # Default timeout in seconds


class ElasticsearchBaseToolset(Toolset):
    """Base class for Elasticsearch toolsets with shared configuration and HTTP logic."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config_class: ClassVar[Type[ElasticsearchConfig]] = ElasticsearchConfig

    def __init__(self, name: str, description: str, tools: list, **kwargs):
        super().__init__(
            name=name,
            enabled=False,
            description=description,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/elasticsearch/",
            icon_url="https://www.elastic.co/favicon.ico",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=tools,
            tags=[ToolsetTag.CORE],
            **kwargs,
        )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if the Elasticsearch configuration is valid and the cluster is reachable."""
        try:
            self.config = ElasticsearchConfig(**config)
            return self._perform_health_check()
        except Exception as e:
            return False, f"Failed to validate Elasticsearch configuration: {str(e)}"

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Perform a health check by querying cluster health."""
        try:
            response = self._make_request("GET", "_cluster/health", timeout=10)
            cluster_name = response.get("cluster_name", "unknown")
            status = response.get("status", "unknown")
            return True, f"Connected to Elasticsearch cluster '{cluster_name}' (status: {status})"
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return False, "Elasticsearch authentication failed. Check your API key or credentials."
            elif e.response.status_code == 403:
                return False, "Elasticsearch access denied. Ensure your credentials have cluster access."
            else:
                return False, f"Elasticsearch API error: {e.response.status_code} - {e.response.text}"
        except requests.exceptions.ConnectionError:
            return False, f"Failed to connect to Elasticsearch at {self.elasticsearch_config.url}"
        except requests.exceptions.Timeout:
            return False, "Elasticsearch health check timed out"
        except Exception as e:
            return False, f"Elasticsearch health check failed: {str(e)}"

    @property
    def elasticsearch_config(self) -> ElasticsearchConfig:
        return self.config  # type: ignore

    def get_example_config(self) -> Dict[str, Any]:
        """Return an example configuration for this toolset."""
        return {
            "url": "https://your-cluster.es.cloud.io",
            "api_key": "{{ env.ELASTICSEARCH_API_KEY }}",
            "verify_ssl": True,
            "timeout": 10,
        }

    def _get_headers(self) -> Dict[str, str]:
        """Build request headers with authentication."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.elasticsearch_config.api_key:
            headers["Authorization"] = f"ApiKey {self.elasticsearch_config.api_key}"
        return headers

    def _get_auth(self) -> Optional[Tuple[str, str]]:
        """Return basic auth tuple if username/password configured."""
        if self.elasticsearch_config.username and self.elasticsearch_config.password:
            return (self.elasticsearch_config.username, self.elasticsearch_config.password)
        return None

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to Elasticsearch.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "_cluster/health")
            params: Query parameters
            body: Request body (JSON)
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response

        Raises:
            requests.exceptions.HTTPError: For HTTP error responses
            requests.exceptions.ConnectionError: For connection problems
            requests.exceptions.Timeout: For timeout errors
        """
        url = f"{self.elasticsearch_config.url.rstrip('/')}/{endpoint.lstrip('/')}"
        timeout = timeout or self.elasticsearch_config.timeout

        response = requests.request(
            method=method,
            url=url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            params=params,
            json=body,
            timeout=timeout,
            verify=self.elasticsearch_config.verify_ssl,
        )
        response.raise_for_status()
        return response.json()


class BaseElasticsearchTool(Tool, ABC):
    """Base class for Elasticsearch tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: ElasticsearchBaseToolset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toolset = toolset

    @property
    def toolset(self) -> ElasticsearchBaseToolset:
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
        """Make a request to Elasticsearch and return structured result."""
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
            error_detail = f"HTTP {e.response.status_code}"
            try:
                error_body = e.response.json()
                if "error" in error_body:
                    error_detail = f"{error_detail}: {json.dumps(error_body['error'])}"
            except Exception:
                error_detail = f"{error_detail}: {e.response.text[:500]}"

            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Elasticsearch request failed for endpoint '{endpoint}': {error_detail}",
                params=params,
            )
        except requests.exceptions.Timeout:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Elasticsearch request timed out for endpoint '{endpoint}'",
                params=params,
            )
        except requests.exceptions.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to connect to Elasticsearch: {str(e)}",
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error querying Elasticsearch: {str(e)}",
                params=params,
            )


class ElasticsearchCat(BaseElasticsearchTool):
    """Thin wrapper around Elasticsearch _cat APIs with server-side filtering."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_cat",
            description=(
                "Query Elasticsearch _cat APIs for cluster information. "
                "Supports: indices, shards, nodes, health, allocation, recovery, segments, aliases. "
                "IMPORTANT: Always use the 'index' parameter when querying shards to filter by specific index."
            ),
            parameters={
                "endpoint": ToolParameter(
                    description=(
                        "The _cat endpoint to query. Valid values: "
                        "indices, shards, nodes, health, allocation, recovery, segments, aliases, "
                        "pending_tasks, thread_pool, plugins, nodeattrs, repositories, snapshots, tasks"
                    ),
                    type="string",
                    required=True,
                ),
                "index": ToolParameter(
                    description=(
                        "Filter by index name or pattern. Supports wildcards (e.g., 'logs-*'). "
                        "REQUIRED for shards, segments, recovery endpoints to avoid returning data for all indices. "
                        "Recommended for indices endpoint when looking for specific indices."
                    ),
                    type="string",
                    required=False,
                ),
                "columns": ToolParameter(
                    description=(
                        "Comma-separated list of columns to return (e.g., 'index,shard,prirep,state,docs'). "
                        "Use this to reduce response size. Run without columns first to see available columns."
                    ),
                    type="string",
                    required=False,
                ),
                "sort": ToolParameter(
                    description="Comma-separated list of columns to sort by (e.g., 'docs:desc,index')",
                    type="string",
                    required=False,
                ),
                "health": ToolParameter(
                    description="Filter by index health (green, yellow, red). Only for indices endpoint.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        endpoint = params["endpoint"]
        index = params.get("index")

        # Build the endpoint path
        if index and endpoint in ("shards", "indices", "segments", "recovery", "aliases"):
            path = f"_cat/{endpoint}/{index}"
        else:
            path = f"_cat/{endpoint}"

        # Build query parameters
        query_params: Dict[str, Any] = {"format": "json"}

        if params.get("columns"):
            query_params["h"] = params["columns"]

        if params.get("sort"):
            query_params["s"] = params["sort"]

        if params.get("health") and endpoint == "indices":
            query_params["health"] = params["health"]

        return self._make_request("GET", path, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        endpoint = params.get("endpoint", "")
        index = params.get("index", "")
        suffix = f" ({index})" if index else ""
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Cat {endpoint}{suffix}"


class ElasticsearchSearch(BaseElasticsearchTool):
    """Execute Elasticsearch Query DSL searches."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_search",
            description=(
                "Execute an Elasticsearch search query using Query DSL. "
                "Supports full Query DSL including bool queries, aggregations, and filters. "
                "Returns up to 100 documents by default (configurable via size parameter)."
            ),
            parameters={
                "index": ToolParameter(
                    description=(
                        "Index name or pattern to search. Supports wildcards (e.g., 'logs-*'). "
                        "Can be comma-separated for multiple indices."
                    ),
                    type="string",
                    required=True,
                ),
                "query": ToolParameter(
                    description=(
                        "Elasticsearch Query DSL query object. Example: "
                        '{"bool": {"must": [{"match": {"level": "ERROR"}}]}}. '
                        "Use match_all for all documents: {}. "
                        "For full-text search use 'match', for exact matches use 'term'."
                    ),
                    type="object",
                    required=False,
                ),
                "size": ToolParameter(
                    description="Maximum number of documents to return (default: 100, max recommended: 500)",
                    type="integer",
                    required=False,
                ),
                "from_offset": ToolParameter(
                    description="Starting offset for pagination (default: 0)",
                    type="integer",
                    required=False,
                ),
                "sort": ToolParameter(
                    description=(
                        "Sort specification. Example: "
                        '[{"@timestamp": "desc"}, {"_score": "asc"}] or just "timestamp:desc"'
                    ),
                    type="array",
                    required=False,
                ),
                "source": ToolParameter(
                    description=(
                        "Fields to include in response. Can be boolean (true/false), "
                        "string (single field), or array of field names"
                    ),
                    type="string",
                    required=False,
                ),
                "aggregations": ToolParameter(
                    description=(
                        "Aggregations to compute. Example: "
                        '{"by_service": {"terms": {"field": "service.keyword", "size": 10}}}. '
                        "Common aggregations: terms (group by), date_histogram, avg, sum, min, max, cardinality."
                    ),
                    type="object",
                    required=False,
                ),
                "profile": ToolParameter(
                    description=(
                        "Enable query profiling to get detailed performance breakdown. "
                        "Shows time spent in each query component. Useful for diagnosing slow queries."
                    ),
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        index = params["index"]
        path = f"{index}/_search"

        # Build request body
        body: Dict[str, Any] = {}

        if params.get("query"):
            body["query"] = params["query"]

        body["size"] = params.get("size", 100)

        if params.get("from_offset"):
            body["from"] = params["from_offset"]

        if params.get("sort"):
            body["sort"] = params["sort"]

        if params.get("source") is not None:
            body["_source"] = params["source"]

        if params.get("aggregations"):
            body["aggs"] = params["aggregations"]

        if params.get("profile"):
            body["profile"] = True

        return self._make_request("POST", path, params, body=body)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        index = params.get("index", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Search {index}"


class ElasticsearchClusterHealth(BaseElasticsearchTool):
    """Get Elasticsearch cluster health status."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_cluster_health",
            description=(
                "Get cluster health information including status (green/yellow/red), "
                "node count, shard counts, and pending tasks."
            ),
            parameters={
                "index": ToolParameter(
                    description="Optional: Get health for specific index or pattern",
                    type="string",
                    required=False,
                ),
                "level": ToolParameter(
                    description=(
                        "Level of detail: 'cluster' (default), 'indices', or 'shards'. "
                        "Higher levels return more detail but more data."
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        index = params.get("index")
        path = f"_cluster/health/{index}" if index else "_cluster/health"

        query_params: Dict[str, Any] = {}
        if params.get("level"):
            query_params["level"] = params["level"]

        return self._make_request("GET", path, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        index = params.get("index", "")
        suffix = f" ({index})" if index else ""
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Cluster health{suffix}"


class ElasticsearchMappings(BaseElasticsearchTool, JsonFilterMixin):
    """Get index mappings (field definitions and types)."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_mappings",
            description=(
                "Get the field mappings (schema) for an index. "
                "Shows field names, data types, and analyzers. "
                "Useful for understanding index structure before writing queries. "
                "For large mappings, use the jq parameter to filter results "
                "(e.g., jq='.*.mappings.properties | keys' to list field names)."
            ),
            parameters=JsonFilterMixin.extend_parameters({
                "index": ToolParameter(
                    description="Index name or pattern to get mappings for",
                    type="string",
                    required=True,
                ),
            }),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        index = params["index"]
        path = f"{index}/_mapping"
        result = self._make_request("GET", path, params)
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        index = params.get("index", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Get mappings for {index}"


class ElasticsearchIndexStats(BaseElasticsearchTool):
    """Get index statistics including document counts, storage, and indexing rates."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_index_stats",
            description=(
                "Get detailed statistics for indices including document count, "
                "store size, indexing rate, and search rate."
            ),
            parameters={
                "index": ToolParameter(
                    description="Index name or pattern. Use '_all' for all indices.",
                    type="string",
                    required=True,
                ),
                "metrics": ToolParameter(
                    description=(
                        "Comma-separated list of metrics to return. Options: "
                        "_all, docs, store, indexing, search, get, merge, refresh, flush, warmer, "
                        "query_cache, fielddata, completion, segments, translog, recovery"
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        index = params["index"]
        metrics = params.get("metrics")

        if metrics:
            path = f"{index}/_stats/{metrics}"
        else:
            path = f"{index}/_stats"

        return self._make_request("GET", path, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        index = params.get("index", "")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Stats for {index}"


class ElasticsearchAllocationExplain(BaseElasticsearchTool):
    """Explain shard allocation decisions and issues."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_allocation_explain",
            description=(
                "Explain why a shard is unassigned or how allocation decisions are made. "
                "Call without parameters to explain the first unassigned shard, "
                "or specify index/shard to explain a specific shard."
            ),
            parameters={
                "index": ToolParameter(
                    description="Index name for specific shard explanation",
                    type="string",
                    required=False,
                ),
                "shard": ToolParameter(
                    description="Shard number (0-based) for specific shard explanation",
                    type="integer",
                    required=False,
                ),
                "primary": ToolParameter(
                    description="True for primary shard, false for replica (default: true)",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        body: Optional[Dict[str, Any]] = None

        if params.get("index") is not None and params.get("shard") is not None:
            body = {
                "index": params["index"],
                "shard": params["shard"],
                "primary": params.get("primary", True),
            }

        return self._make_request("GET", "_cluster/allocation/explain", params, body=body)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        index = params.get("index", "")
        shard = params.get("shard", "")
        if index and shard is not None:
            return f"{toolset_name_for_one_liner(self._toolset.name)}: Explain allocation for {index} shard {shard}"
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Explain unassigned shard"


class ElasticsearchNodesStats(BaseElasticsearchTool):
    """Get node-level statistics."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_nodes_stats",
            description=(
                "Get statistics for cluster nodes including JVM, OS, process, "
                "thread pool, filesystem, transport, and HTTP metrics."
            ),
            parameters={
                "node_id": ToolParameter(
                    description="Specific node ID or name. Use '_local' for current node, '_all' for all nodes.",
                    type="string",
                    required=False,
                ),
                "metrics": ToolParameter(
                    description=(
                        "Comma-separated list of metrics. Options: "
                        "_all, breaker, fs, http, indices, jvm, os, process, thread_pool, transport, discovery"
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        node_id = params.get("node_id", "_all")
        metrics = params.get("metrics")

        if metrics:
            path = f"_nodes/{node_id}/stats/{metrics}"
        else:
            path = f"_nodes/{node_id}/stats"

        return self._make_request("GET", path, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        node_id = params.get("node_id", "_all")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Node stats ({node_id})"


class ElasticsearchListIndices(BaseElasticsearchTool, JsonFilterMixin):
    """List indices matching a pattern with full server-side filtering support."""

    def __init__(self, toolset: ElasticsearchBaseToolset):
        super().__init__(
            toolset=toolset,
            name="elasticsearch_list_indices",
            description=(
                "List Elasticsearch indices matching a pattern. "
                "Returns index names, document counts, and storage size. "
                "Supports server-side sorting and filtering for efficient queries on large clusters."
            ),
            parameters=JsonFilterMixin.extend_parameters({
                "pattern": ToolParameter(
                    description=(
                        "Index name pattern to match. Supports wildcards (e.g., 'logs-*', 'app-*'). "
                        "Use '*' to list all indices."
                    ),
                    type="string",
                    required=False,
                ),
                "sort": ToolParameter(
                    description=(
                        "Sort by column. Format: 'column' or 'column:desc'. "
                        "Examples: 'store.size:desc' (largest first), 'docs.count:desc', 'index'. "
                        "Default: 'index' (alphabetical)."
                    ),
                    type="string",
                    required=False,
                ),
                "columns": ToolParameter(
                    description=(
                        "Comma-separated columns to return. Available: index, health, status, pri, rep, "
                        "docs.count, docs.deleted, store.size, pri.store.size, creation.date, creation.date.string. "
                        "Default: 'index,health,status,docs.count,store.size'"
                    ),
                    type="string",
                    required=False,
                ),
                "health": ToolParameter(
                    description="Filter by index health: green, yellow, or red",
                    type="string",
                    required=False,
                ),
                "bytes": ToolParameter(
                    description="Unit for byte sizes: b, kb, mb, gb, tb, pb. Default: human-readable.",
                    type="string",
                    required=False,
                ),
                "pri": ToolParameter(
                    description="If true, return only primary shard statistics",
                    type="boolean",
                    required=False,
                ),
                "expand_wildcards": ToolParameter(
                    description="Which indices to expand wildcards to: open, closed, hidden, none, all. Default: open",
                    type="string",
                    required=False,
                ),
            }),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        pattern = params.get("pattern", "*")
        path = f"_cat/indices/{pattern}"

        query_params: Dict[str, Any] = {"format": "json"}

        # Columns (h parameter)
        columns = params.get("columns", "index,health,status,docs.count,store.size")
        query_params["h"] = columns

        # Sort (s parameter)
        sort = params.get("sort", "index")
        query_params["s"] = sort

        # Health filter
        if params.get("health"):
            query_params["health"] = params["health"]

        # Byte units
        if params.get("bytes"):
            query_params["bytes"] = params["bytes"]

        # Primary only
        if params.get("pri"):
            query_params["pri"] = "true"

        # Expand wildcards
        if params.get("expand_wildcards"):
            query_params["expand_wildcards"] = params["expand_wildcards"]

        result = self._make_request("GET", path, params, query_params=query_params)
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        pattern = params.get("pattern", "*")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List indices ({pattern})"


# =============================================================================
# Toolset Definitions (must be after all tool classes)
# =============================================================================


class ElasticsearchDataToolset(ElasticsearchBaseToolset):
    """Toolset for querying data stored in Elasticsearch/OpenSearch.

    This toolset provides tools for searching logs, metrics, and documents.
    Requires only index-level read permissions (no cluster-level access needed).
    """

    def __init__(self):
        super().__init__(
            name="elasticsearch/data",
            description="Search and query data in Elasticsearch/OpenSearch indices - logs, metrics, documents",
            tools=[],
        )
        # Initialize tools after super().__init__() - update the pydantic field
        self.tools = [
            ElasticsearchSearch(self),
            ElasticsearchMappings(self),
            ElasticsearchListIndices(self),
        ]


class ElasticsearchClusterToolset(ElasticsearchBaseToolset):
    """Toolset for troubleshooting Elasticsearch/OpenSearch cluster health.

    This toolset provides tools for diagnosing cluster issues like unassigned
    shards, node problems, and resource usage. Requires cluster-level permissions.
    """

    def __init__(self):
        super().__init__(
            name="elasticsearch/cluster",
            description="Troubleshoot Elasticsearch/OpenSearch cluster health - shards, nodes, allocation",
            tools=[],
        )
        # Initialize tools after super().__init__() - update the pydantic field
        self.tools = [
            ElasticsearchCat(self),
            ElasticsearchClusterHealth(self),
            ElasticsearchIndexStats(self),
            ElasticsearchAllocationExplain(self),
            ElasticsearchNodesStats(self),
        ]


# Backwards compatibility alias
ElasticsearchToolset = ElasticsearchClusterToolset
