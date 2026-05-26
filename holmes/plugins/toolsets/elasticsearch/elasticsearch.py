import json
import logging
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

import requests  # type: ignore[import-untyped]
from pydantic import ConfigDict, Field, ValidationError, model_validator
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
from holmes.plugins.toolsets.json_filter_mixin import JsonFilterMixin
from holmes.plugins.toolsets.utils import toolset_name_for_one_liner
from holmes.utils.pydantic_utils import ToolsetConfig

logger = logging.getLogger(__name__)

ELASTICSEARCH_INSTANCE_PARAM_DESCRIPTION = (
    "Name of the Elasticsearch instance to query. Required when more than one "
    "instance is configured. Leave empty when only a single instance is configured."
)
ELASTICSEARCH_INSTANCE_PARAM = ToolParameter(
    description=ELASTICSEARCH_INSTANCE_PARAM_DESCRIPTION,
    type="string",
    required=False,
)


class ElasticsearchInstance(ToolsetConfig):
    """Connection details for a single Elasticsearch/OpenSearch target.

    Used when configuring multiple clusters via `ElasticsearchConfig.instances`.
    Auth and SSL/timeout fields default to `None` so the multi-instance
    config can detect whether the user explicitly set them on this instance
    vs inheriting the top-level global default.
    """

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "url": "api_url",
    }

    name: str = Field(
        title="Name",
        description="Stable identifier the LLM uses to select this instance",
        examples=["prod-eu", "staging-us"],
    )
    api_url: str = Field(
        title="API URL",
        description="Elasticsearch/OpenSearch base URL for this instance",
    )
    api_key: Optional[str] = Field(default=None, json_schema_extra={"format": "password"})
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, json_schema_extra={"format": "password"})
    client_cert: Optional[str] = None
    client_key: Optional[str] = None
    # `None` means "inherit from the top-level global".
    verify_ssl: Optional[bool] = None
    timeout_seconds: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_auth_and_mtls(self) -> "ElasticsearchInstance":
        if self.api_key and (self.username or self.password):
            raise ValueError(
                f"Elasticsearch instance '{self.name}': use `api_key` OR `username` + `password`, not both"
            )
        if bool(self.username) != bool(self.password):
            raise ValueError(
                f"Elasticsearch instance '{self.name}': `username` and `password` must be set together"
            )
        if self.client_cert and not self.client_key:
            raise ValueError(
                f"Elasticsearch instance '{self.name}': `client_key` is required when `client_cert` is set"
            )
        if self.client_key and not self.client_cert:
            raise ValueError(
                f"Elasticsearch instance '{self.name}': `client_cert` is required when `client_key` is set"
            )
        return self


def build_auth(instance: ElasticsearchInstance) -> Optional[HTTPBasicAuth]:
    if instance.username and instance.password:
        return HTTPBasicAuth(instance.username, instance.password)
    return None


class ElasticsearchConfig(ToolsetConfig):
    """Configuration for Elasticsearch/OpenSearch API access.

    Single-instance (legacy) configuration:
    ```yaml
    api_url: "https://your-cluster.es.cloud.io"
    api_key: "base64_encoded_api_key"
    ```

    Multi-instance configuration:
    ```yaml
    # Top-level fields act as global defaults inherited by any instance
    # that doesn't override them.
    username: elastic
    password: "{{ env.ES_GLOBAL_PASSWORD }}"
    instances:
      - name: prod-eu
        api_url: https://prod-eu.es.internal:9200
      - name: prod-us
        api_url: https://prod-us.es.internal:9200
        password: "{{ env.ES_US_PASSWORD }}"   # per-instance override
    ```
    """

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "url": "api_url",
        "timeout": "timeout_seconds",
        "ca_cert": None,
    }

    api_url: Optional[str] = Field(
        default=None,
        title="API URL",
        description="Elasticsearch/OpenSearch base URL (single-instance shape). Omit when using `instances`.",
        examples=["https://your-cluster.es.cloud.io"],
    )
    api_key: Optional[str] = Field(
        default=None,
        title="API Key",
        description="API key for authentication (preferred over basic auth when available)",
        examples=["{{ env.ELASTICSEARCH_API_KEY }}"],
    )
    username: Optional[str] = Field(
        default=None,
        title="Username",
        description="Username for basic auth authentication (used if api_key is not provided)",
    )
    password: Optional[str] = Field(
        default=None,
        title="Password",
        description="Password for basic auth authentication (used if api_key is not provided)",
    )
    client_cert: Optional[str] = Field(
        default=None,
        title="Client Certificate",
        description="Path to client certificate file for mTLS authentication (PEM format)",
        examples=["/path/to/client.crt", "{{ env.ELASTICSEARCH_CLIENT_CERT }}"],
    )
    client_key: Optional[str] = Field(
        default=None,
        title="Client Key",
        description="Path to client private key file for mTLS authentication (PEM format)",
        examples=["/path/to/client.key", "{{ env.ELASTICSEARCH_CLIENT_KEY }}"],
    )
    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description="Whether to verify SSL certificates. For custom CAs, use the global CERTIFICATE env var instead.",
    )
    timeout_seconds: int = Field(
        default=10,
        title="Timeout Seconds",
        description="Default request timeout in seconds",
    )
    instances: Optional[List[ElasticsearchInstance]] = Field(
        default=None,
        title="Instances",
        description=(
            "List of Elasticsearch instances for multi-cluster routing. "
            "When set, top-level connection fields act as global defaults inherited "
            "by any instance that doesn't override them."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_instances(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("instances")
        if not raw:
            return data
        coerced: List[ElasticsearchInstance] = []
        for idx, item in enumerate(raw):
            if isinstance(item, ElasticsearchInstance):
                coerced.append(item)
                continue
            if not isinstance(item, dict):
                raise ValueError(
                    f"`instances[{idx}]` must be a dict, got {type(item).__name__}"
                )
            try:
                coerced.append(ElasticsearchInstance(**item))
            except ValidationError as e:
                raise ValueError(
                    f"Elasticsearch instance '{item.get('name', idx)}' is invalid: {e}"
                ) from e
        data["instances"] = coerced
        return data

    @model_validator(mode="after")
    def _normalize_and_resolve_globals(self) -> "ElasticsearchConfig":
        # Top-level auth must follow the same XOR rule as per-instance auth so
        # mixed credentials are rejected up front rather than silently letting
        # api_key win over username/password in the fall-through below.
        if self.api_key and (self.username or self.password):
            raise ValueError(
                "Elasticsearch config: use top-level `api_key` OR `username` + `password`, not both"
            )
        if bool(self.username) != bool(self.password):
            raise ValueError(
                "Elasticsearch config: top-level `username` and `password` must be set together"
            )
        if self.client_cert and not self.client_key:
            raise ValueError("client_key is required when client_cert is set")
        if self.client_key and not self.client_cert:
            raise ValueError("client_cert is required when client_key is set")

        if not self.instances:
            if not self.api_url:
                raise ValueError(
                    "Either `instances` or top-level `api_url` is required for the Elasticsearch toolset"
                )
            # Backwards compat: synthesize a single "default" instance so the
            # rest of the toolset only has to deal with the multi-instance code
            # path.
            self.instances = [
                ElasticsearchInstance(
                    name="default",
                    api_url=self.api_url,
                    api_key=self.api_key,
                    username=self.username,
                    password=self.password,
                    client_cert=self.client_cert,
                    client_key=self.client_key,
                    verify_ssl=self.verify_ssl,
                    timeout_seconds=self.timeout_seconds,
                )
            ]
            return self

        if self.api_url:
            logger.warning(
                "ElasticsearchConfig: top-level `api_url` is ignored when `instances` is set. "
                "Move connection fields into an entry under `instances`."
            )

        seen: set[str] = set()
        for inst in self.instances:
            if inst.name in seen:
                raise ValueError(f"Duplicate Elasticsearch instance name: '{inst.name}'")
            seen.add(inst.name)
            if inst.verify_ssl is None:
                inst.verify_ssl = self.verify_ssl
            if inst.timeout_seconds is None:
                inst.timeout_seconds = self.timeout_seconds
            # mTLS fall-through: instances without their own client cert inherit
            # the global cert/key pair.
            if not inst.client_cert and self.client_cert and self.client_key:
                inst.client_cert = self.client_cert
                inst.client_key = self.client_key
            # Auth fall-through: instances without their own auth inherit the
            # global credentials.
            if not (inst.api_key or inst.username or inst.password):
                if self.api_key:
                    inst.api_key = self.api_key
                elif self.username and self.password:
                    inst.username = self.username
                    inst.password = self.password
        return self


class ElasticsearchBaseToolset(Toolset):
    """Base class for Elasticsearch toolsets with shared configuration and HTTP logic."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config_classes: ClassVar[list[Type[ElasticsearchConfig]]] = [ElasticsearchConfig]

    def __init__(self, name: str, description: str, tools: list, **kwargs):
        super().__init__(
            name=name,
            enabled=False,
            description=description,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/elasticsearch/",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/elasticsearch.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=tools,
            tags=[ToolsetTag.CORE],
            **kwargs,
        )
        self._instances: Dict[str, ElasticsearchInstance] = {}

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if the Elasticsearch configuration is valid and the cluster is reachable."""
        try:
            config_class = self.config_classes[0] if self.config_classes else ElasticsearchConfig
            self.config = config_class(**config)
        except Exception as e:
            return False, f"Failed to validate Elasticsearch configuration: {str(e)}"

        # `_normalize_and_resolve_globals` guarantees a non-empty `instances`
        # list (synthesizing a single "default" for the legacy flat shape).
        instances = self.elasticsearch_config.instances or []
        self._instances = {i.name: i for i in instances}
        self._prune_tools_for_single_instance()
        return self._perform_health_check()

    def _prune_tools_for_single_instance(self) -> None:
        """Hide the multi-instance affordances when only one instance is configured.

        When there's a single instance, the `elasticsearch_instance` parameter
        and the `elasticsearch_{data,cluster}_list_instances` discovery tool
        add no value and cost tokens on every tool call. Drop them so the
        LLM's tool surface matches the simpler config.
        """
        if len(self._instances) != 1:
            return
        self.tools = [t for t in self.tools if not isinstance(t, ElasticsearchListInstances)]
        for tool in self.tools:
            tool.parameters.pop("elasticsearch_instance", None)

    def _perform_health_check(self) -> Tuple[bool, str]:
        """Probe `_cluster/health` on each configured instance.

        Tolerant: succeeds as long as at least one instance is reachable; the
        toolset still loads with the healthy ones. Each failure is captured
        with the instance name, status code, and response body so the LLM (and
        any human reading the status string) can self-correct.
        """
        failures: List[str] = []
        successes: List[str] = []
        for instance in self._instances.values():
            ok, msg = self._health_check_instance(instance)
            if ok:
                successes.append(msg)
            else:
                failures.append(msg)
        return self._aggregate_health_results(failures, successes)

    def _health_check_instance(
        self, instance: ElasticsearchInstance
    ) -> Tuple[bool, str]:
        try:
            data = self._make_request(instance, "GET", "_cluster/health", timeout=10)
            cluster_name = data.get("cluster_name", "unknown")
            status = data.get("status", "unknown")
            return (
                True,
                f"[{instance.name}] Connected to '{cluster_name}' (status: {status})",
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code
            body = e.response.text[:500] if e.response is not None else ""
            if status_code == 401:
                return (
                    False,
                    f"[{instance.name}] Authentication failed for {instance.api_url}. "
                    "Check api_key or username/password.",
                )
            if status_code == 403:
                return (
                    False,
                    f"[{instance.name}] Access denied at {instance.api_url}. "
                    "Credentials lack cluster access.",
                )
            return (
                False,
                f"[{instance.name}] HTTP {status_code} from {instance.api_url}: {body}",
            )
        except requests.exceptions.SSLError as e:
            error_msg = str(e)
            if (
                "certificate required" in error_msg.lower()
                or "sslcertverificationerror" in error_msg.lower()
            ):
                return (
                    False,
                    f"[{instance.name}] SSL/TLS error at {instance.api_url}: {error_msg}. "
                    "If the server requires mTLS, configure client_cert and client_key. "
                    "If using a private CA, set the CERTIFICATE env var (base64-encoded CA cert).",
                )
            return False, f"[{instance.name}] SSL error at {instance.api_url}: {error_msg}"
        except requests.exceptions.ConnectionError as e:
            return (
                False,
                f"[{instance.name}] Failed to connect to {instance.api_url}: {e}",
            )
        except requests.exceptions.Timeout:
            return False, f"[{instance.name}] Health check timed out for {instance.api_url}"
        except Exception as e:
            return False, f"[{instance.name}] Health check failed: {str(e)}"

    def _aggregate_health_results(
        self, failures: List[str], successes: List[str]
    ) -> Tuple[bool, str]:
        """Tolerant aggregation: succeed if any instance is reachable.

        Returns `(True, summary)` when at least one instance is healthy; the
        summary lists healthy connections and notes any failures so they're
        visible in the toolset status. Returns `(False, joined_errors)` only
        when every instance failed.
        """
        total = len(failures) + len(successes)
        if not successes:
            return False, "\n".join(failures) or "No Elasticsearch instances configured"
        if failures:
            logger.warning(
                f"{self.name}: {len(successes)}/{total} instance(s) healthy. "
                f"Failed: {failures}"
            )
            return True, (
                "; ".join(successes)
                + "; failed: "
                + " | ".join(failures)
            )
        return True, "; ".join(successes)

    def _get_instance(self, params: Dict[str, Any]) -> ElasticsearchInstance:
        """Resolve which Elasticsearch instance a tool call should target.

        Auto-selects when only one is configured. Otherwise requires
        `elasticsearch_instance` in params. Raises `ValueError` with a helpful
        message listing the configured names when missing or unknown.
        """
        configured = sorted(self._instances)
        requested = params.get("elasticsearch_instance")
        if not requested:
            if len(self._instances) == 1:
                return next(iter(self._instances.values()))
            raise ValueError(
                f"`elasticsearch_instance` is required (configured: {configured})"
            )
        if requested not in self._instances:
            raise ValueError(
                f"Unknown elasticsearch_instance '{requested}'. Configured: {configured}"
            )
        return self._instances[requested]

    @property
    def elasticsearch_config(self) -> ElasticsearchConfig:
        return self.config  # type: ignore

    def _build_headers(self, instance: ElasticsearchInstance) -> Dict[str, str]:
        """Build request headers with authentication for the given instance."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if instance.api_key:
            headers["Authorization"] = f"ApiKey {instance.api_key}"
        return headers

    def _make_request(
        self,
        instance: ElasticsearchInstance,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to a specific Elasticsearch instance.

        Args:
            instance: The target Elasticsearch instance (resolved via `_get_instance`).
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
        url = f"{instance.api_url.rstrip('/')}/{endpoint.lstrip('/')}"
        effective_timeout = timeout or instance.timeout_seconds or 10
        cert: Optional[Tuple[str, str]] = (
            (instance.client_cert, instance.client_key)
            if instance.client_cert and instance.client_key
            else None
        )

        response = requests.request(
            method=method,
            url=url,
            headers=self._build_headers(instance),
            auth=build_auth(instance),
            cert=cert,
            params=params,
            json=body,
            timeout=effective_timeout,
            verify=bool(instance.verify_ssl),
        )
        response.raise_for_status()
        return response.json()


def _instance_error_result(params: dict, err: Exception) -> StructuredToolResult:
    return StructuredToolResult(
        status=StructuredToolResultStatus.ERROR, error=str(err), params=params
    )


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
        instance: ElasticsearchInstance,
        method: str,
        endpoint: str,
        params: dict,
        query_params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> StructuredToolResult:
        """Make a request to a specific Elasticsearch instance and return a structured result."""
        try:
            data = self._toolset._make_request(
                instance,
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
                error=(
                    f"[{instance.name}] Elasticsearch request failed for endpoint "
                    f"'{endpoint}': {error_detail}"
                ),
                params=params,
            )
        except requests.exceptions.Timeout:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=(
                    f"[{instance.name}] Elasticsearch request timed out for endpoint "
                    f"'{endpoint}'"
                ),
                params=params,
            )
        except requests.exceptions.ConnectionError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"[{instance.name}] Failed to connect to Elasticsearch: {str(e)}",
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"[{instance.name}] Unexpected error querying Elasticsearch: {str(e)}",
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
                "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
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
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

        endpoint = params["endpoint"]
        index = params.get("index")

        # Build the endpoint path
        if index and endpoint in (
            "shards",
            "indices",
            "segments",
            "recovery",
            "aliases",
        ):
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

        return self._make_request(instance, "GET", path, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        endpoint = params.get("endpoint", "")
        index = params.get("index", "")
        suffix = f" ({index})" if index else ""
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: Cat {endpoint}{suffix}"
        )


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
                "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
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
                        "Fields to include/exclude in response. Supported formats:\n"
                        "• Array: ['field1', 'field2'] - Include only these fields\n"
                        "• String: 'field1' - Include single field\n"
                        "• Object: {\"includes\": [\"trace.*\", \"span.*\"], \"excludes\": [\"*.body\", \"*.stack_trace\"]}\n"
                        "  - Use wildcards (*) for pattern matching\n"
                        "  - Excludes are useful for filtering large fields (http.request.body, error.stack_trace, http.response.*)\n"
                        "• Boolean: false - Exclude all source (metadata only)\n\n"
                        "Examples:\n"
                        "- Trace query: {\"includes\": [\"trace.*\", \"span.*\", \"service.*\"], \"excludes\": [\"*.request.*\", \"*.response.*\"]}\n"
                        "- Logs: [\"@timestamp\", \"message\", \"level\", \"service.name\"]"
                    ),
                    type="object",
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
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

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

        return self._make_request(instance, "POST", path, params, body=body)

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
                "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
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
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

        index = params.get("index")
        path = f"_cluster/health/{index}" if index else "_cluster/health"

        query_params: Dict[str, Any] = {}
        if params.get("level"):
            query_params["level"] = params["level"]

        return self._make_request(instance, "GET", path, params, query_params=query_params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        index = params.get("index", "")
        suffix = f" ({index})" if index else ""
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: Cluster health{suffix}"
        )


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
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
                    "index": ToolParameter(
                        description="Index name or pattern to get mappings for",
                        type="string",
                        required=True,
                    ),
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

        index = params["index"]
        path = f"{index}/_mapping"
        result = self._make_request(instance, "GET", path, params)
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
                "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
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
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

        index = params["index"]
        metrics = params.get("metrics")

        if metrics:
            path = f"{index}/_stats/{metrics}"
        else:
            path = f"{index}/_stats"

        return self._make_request(instance, "GET", path, params)

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
                "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
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
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

        body: Optional[Dict[str, Any]] = None

        if params.get("index") is not None and params.get("shard") is not None:
            body = {
                "index": params["index"],
                "shard": params["shard"],
                "primary": params.get("primary", True),
            }

        return self._make_request(
            instance, "GET", "_cluster/allocation/explain", params, body=body
        )

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
                "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
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
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

        node_id = params.get("node_id", "_all")
        metrics = params.get("metrics")

        if metrics:
            path = f"_nodes/{node_id}/stats/{metrics}"
        else:
            path = f"_nodes/{node_id}/stats"

        return self._make_request(instance, "GET", path, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        node_id = params.get("node_id", "_all")
        return (
            f"{toolset_name_for_one_liner(self._toolset.name)}: Node stats ({node_id})"
        )


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
            parameters=JsonFilterMixin.extend_parameters(
                {
                    "elasticsearch_instance": ELASTICSEARCH_INSTANCE_PARAM,
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
                }
            ),
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            instance = self._toolset._get_instance(params)
        except ValueError as e:
            return _instance_error_result(params, e)

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

        result = self._make_request(instance, "GET", path, params, query_params=query_params)
        return self.filter_result(result, params)

    def get_parameterized_one_liner(self, params: Dict) -> str:
        pattern = params.get("pattern", "*")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List indices ({pattern})"


class ElasticsearchListInstances(Tool):
    """List configured Elasticsearch instances for the LLM to discover routing targets."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: ElasticsearchBaseToolset):
        # Scope the tool name to the toolset (`elasticsearch/data` →
        # `elasticsearch_data_list_instances`) so the data and cluster
        # toolsets register distinct discovery tools instead of colliding
        # on a single shared name when both are multi-instance.
        toolset_suffix = toolset.name.split("/")[-1]
        super().__init__(
            name=f"elasticsearch_{toolset_suffix}_list_instances",
            description=(
                f"List the Elasticsearch instances configured for the "
                f"`{toolset.name}` toolset. Returns each instance's name and "
                f"api_url so subsequent tool calls can target the right one via "
                f"the `elasticsearch_instance` parameter."
            ),
            parameters={},
        )
        self._toolset = toolset

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        instances = [
            {"name": inst.name, "api_url": inst.api_url}
            for inst in self._toolset._instances.values()
        ]
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data={"instances": instances},
            params=params,
        )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List instances"


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
            ElasticsearchListInstances(self),
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
            ElasticsearchListInstances(self),
            ElasticsearchCat(self),
            ElasticsearchClusterHealth(self),
            ElasticsearchIndexStats(self),
            ElasticsearchAllocationExplain(self),
            ElasticsearchNodesStats(self),
        ]


# Backwards compatibility alias
ElasticsearchToolset = ElasticsearchClusterToolset
