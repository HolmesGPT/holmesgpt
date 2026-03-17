import json
import logging
import os
import re
from abc import ABC
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from pydantic import ConfigDict, Field

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
from holmes.utils.pydantic_utils import ToolsetConfig

import pymongo

logger = logging.getLogger(__name__)

# Aggregation stages that write data — blocked in read-only mode
_WRITE_STAGES = {"$out", "$merge"}


def _parse_json_param(value: str, param_name: str) -> Any:
    """Parse a JSON string parameter, raising ValueError with a clear message on failure."""
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON for '{param_name}': {e}. Received: {value[:200]}")


def _serialize_value(val: Any) -> Any:
    """Convert BSON values to JSON-safe types."""
    if val is None:
        return None
    if isinstance(val, (int, float, bool, str)):
        return val
    if isinstance(val, bytes):
        return val.hex()
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_serialize_value(v) for v in val]
    # ObjectId, datetime, Decimal128, etc.
    return str(val)


class MongoDBConfig(ToolsetConfig):
    """Configuration for the MongoDB toolset.

    Example configuration:
    ```yaml
    orders-mongo:
      type: mongodb
      enabled: true
      config:
        connection_url: "mongodb://user:pass@host:27017/orders"
        default_database: "orders"
        read_only: true
        verify_ssl: true
        max_rows: 200
      llm_instructions: "This is the orders database for our e-commerce platform"
    ```
    """


    connection_url: str = Field(
        title="Connection URL",
        description=(
            "MongoDB connection string. "
            "Supports standard mongodb:// and mongodb+srv:// schemes."
        ),
        examples=[
            "mongodb://user:pass@host:27017/mydb",
            "mongodb+srv://user:pass@cluster.mongodb.net/mydb",
        ],
    )

    default_database: Optional[str] = Field(
        default=None,
        title="Default Database",
        description=(
            "Default database name to use. If not set, the database from the connection URL is used. "
            "Tools can override this per-call with the 'database' parameter."
        ),
    )

    read_only: bool = Field(
        default=True,
        title="Read-Only Mode",
        description=(
            "When True (default), only read operations are allowed (find, aggregate without $out/$merge). "
            "Warning: Disabling read-only mode grants full database access to the LLM."
        ),
    )

    verify_ssl: bool = Field(
        default=True,
        title="Verify SSL",
        description=(
            "When True (default), verify SSL certificates for MongoDB connections. "
            "Set to False for self-signed certificates or development environments."
        ),
    )

    max_rows: int = Field(
        default=200,
        title="Maximum Rows",
        description=(
            "Maximum number of documents to return from query results. "
            "Limits result size to prevent token overflow. "
            "Default: 200 documents."
        ),
        ge=1,
        le=10000,
    )

    timeout_seconds: int = Field(
        default=30,
        title="Timeout Seconds",
        description="Connection and operation timeout in seconds.",
        ge=1,
        le=300,
    )


class MongoDBToolset(Toolset):
    """Toolset for querying MongoDB databases via pymongo.

    By default, provides read-only access to any MongoDB instance.
    Write operations are blocked unless explicitly enabled via configuration.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config_classes: ClassVar[list[Type[MongoDBConfig]]] = [MongoDBConfig]

    _client: Optional[pymongo.MongoClient] = None

    def __init__(self, name: str = "mongodb", **kwargs: Any):
        llm_instructions = kwargs.pop("llm_instructions", None)
        enabled = kwargs.pop("enabled", False)
        kwargs.pop("type", None)

        description = kwargs.pop("description", None) or f"Query {name} MongoDB database"

        super().__init__(
            name=name,
            enabled=enabled,
            description=description,
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/mongodb/",
            icon_url="https://www.mongodb.com/favicon.ico",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
            **kwargs,
        )
        tool_prefix = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
        self.tools = [
            MongoDBQuery(self, tool_prefix),
            MongoDBAggregate(self, tool_prefix),
            MongoDBListCollections(self, tool_prefix),
            MongoDBCollectionSchema(self, tool_prefix),
            MongoDBListDatabases(self, tool_prefix),
            MongoDBServerStatus(self, tool_prefix),
            MongoDBCurrentOp(self, tool_prefix),
        ]
        self._load_llm_instructions_from_file(
            os.path.dirname(__file__), "instructions.jinja2"
        )

        if llm_instructions:
            self.llm_instructions = (
                (self.llm_instructions or "")
                + "\n\n## Database-Specific Instructions\n\n"
                + llm_instructions
            )

    def prerequisites_callable(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            self.config = MongoDBConfig(**config)
            return self._perform_health_check()
        except Exception as e:
            return False, f"Failed to validate MongoDB configuration: {e}"

    def _perform_health_check(self) -> Tuple[bool, str]:
        try:
            self._client = self._create_client()
            self._client.admin.command("ping")
            # Resolve default database name
            if not self.mongodb_config.default_database:
                db_name = pymongo.uri_parser.parse_uri(self.mongodb_config.connection_url).get("database")
                if db_name:
                    self.mongodb_config.default_database = db_name
            return True, "Connected to MongoDB"
        except Exception as e:
            self._client = None
            return False, f"MongoDB connection failed: {e}"

    def _create_client(self) -> pymongo.MongoClient:
        kwargs: Dict[str, Any] = {
            "serverSelectionTimeoutMS": self.mongodb_config.timeout_seconds * 1000,
            "connectTimeoutMS": self.mongodb_config.timeout_seconds * 1000,
            "socketTimeoutMS": self.mongodb_config.timeout_seconds * 1000,
        }
        if not self.mongodb_config.verify_ssl:
            kwargs["tlsAllowInvalidCertificates"] = True

        return pymongo.MongoClient(
            self.mongodb_config.connection_url,
            **kwargs,
        )

    @property
    def mongodb_config(self) -> MongoDBConfig:
        return self.config  # type: ignore

    def _get_database(self, database: Optional[str] = None) -> pymongo.database.Database:
        db_name = database or self.mongodb_config.default_database
        if not db_name:
            raise ValueError(
                "No database specified. Provide a 'database' parameter or set 'default_database' in config."
            )
        return self._client[db_name]  # type: ignore

    def execute_find(
        self,
        collection: str,
        filter_doc: Optional[Dict] = None,
        projection: Optional[Dict] = None,
        sort: Optional[List] = None,
        limit: Optional[int] = None,
        database: Optional[str] = None,
    ) -> Dict[str, Any]:
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be a positive integer, got {limit}")
        effective_limit = min(
            limit or self.mongodb_config.max_rows,
            self.mongodb_config.max_rows,
        )

        db = self._get_database(database)
        coll = db[collection]
        cursor = coll.find(
            filter=filter_doc or {},
            projection=projection,
        )
        if sort:
            cursor = cursor.sort(sort)
        cursor = cursor.limit(effective_limit + 1)

        docs = list(cursor)
        truncated = len(docs) > effective_limit
        if truncated:
            docs = docs[:effective_limit]

        return {
            "documents": [_serialize_value(doc) for doc in docs],
            "count": len(docs),
            "truncated": truncated,
        }

    def execute_aggregate(
        self,
        collection: str,
        pipeline: List[Dict],
        database: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.mongodb_config.read_only:
            for stage in pipeline:
                for key in stage:
                    if key in _WRITE_STAGES:
                        raise ValueError(
                            f"Write stage '{key}' is not allowed in read-only mode. "
                            f"Only read-only aggregation stages are permitted."
                        )

        db = self._get_database(database)
        coll = db[collection]
        results = list(coll.aggregate(pipeline))

        max_docs = self.mongodb_config.max_rows
        truncated = len(results) > max_docs
        if truncated:
            results = results[:max_docs]

        return {
            "documents": [_serialize_value(doc) for doc in results],
            "count": len(results),
            "truncated": truncated,
        }

    def list_collections(self, database: Optional[str] = None) -> Dict[str, Any]:
        db = self._get_database(database)
        collections = db.list_collection_names()
        return {
            "database": db.name,
            "collections": sorted(collections),
            "total_count": len(collections),
        }

    def get_collection_schema(
        self,
        collection: str,
        database: Optional[str] = None,
        sample_size: int = 10,
    ) -> Dict[str, Any]:
        db = self._get_database(database)
        coll = db[collection]

        # Sample documents to infer schema
        sample_docs = list(coll.find().limit(sample_size))
        field_types: Dict[str, set] = {}
        for doc in sample_docs:
            self._extract_field_types(doc, field_types, prefix="")

        schema_fields = {
            field: sorted(types) for field, types in sorted(field_types.items())
        }

        # Get indexes
        indexes = []
        for idx_name, idx_info in coll.index_information().items():
            indexes.append({
                "name": idx_name,
                "keys": idx_info.get("key", []),
                "unique": idx_info.get("unique", False),
            })

        # Get estimated document count
        estimated_count = coll.estimated_document_count()

        return {
            "collection": collection,
            "database": db.name,
            "estimated_document_count": estimated_count,
            "fields": schema_fields,
            "indexes": indexes,
            "sample_size": len(sample_docs),
        }

    def _extract_field_types(
        self, doc: Dict, field_types: Dict[str, set], prefix: str
    ) -> None:
        for key, value in doc.items():
            full_key = f"{prefix}.{key}" if prefix else key
            type_name = type(value).__name__
            if full_key not in field_types:
                field_types[full_key] = set()
            field_types[full_key].add(type_name)

            if isinstance(value, dict):
                self._extract_field_types(value, field_types, full_key)

    def get_server_status(self, sections: Optional[List[str]] = None) -> Dict[str, Any]:
        """Run serverStatus and return selected sections for diagnostics."""
        status = self._client.admin.command("serverStatus")  # type: ignore
        # Always include these overview fields
        result: Dict[str, Any] = {
            "host": status.get("host"),
            "version": status.get("version"),
            "uptime_seconds": status.get("uptimeEstimate", status.get("uptime")),
        }

        # Default sections that are most useful for performance diagnostics
        default_sections = [
            "connections", "opcounters", "mem", "locks",
            "globalLock", "network", "wiredTiger",
        ]
        requested = sections or default_sections
        for section in requested:
            if section in status:
                result[section] = status[section]

        # Include replication info if available
        if "repl" in status:
            result["repl"] = status["repl"]

        return _serialize_value(result)

    def get_current_op(
        self,
        min_duration_ms: Optional[int] = None,
        active_only: bool = True,
    ) -> Dict[str, Any]:
        """Run currentOp to find active/slow operations."""
        filter_doc: Dict[str, Any] = {}
        if active_only:
            filter_doc["active"] = True
        if min_duration_ms is not None:
            filter_doc["microsecs_running"] = {"$gte": min_duration_ms * 1000}

        result = self._client.admin.command("currentOp", **filter_doc)  # type: ignore
        ops = result.get("inprog", [])

        # Limit output to prevent token overflow
        max_ops = self.mongodb_config.max_rows
        truncated = len(ops) > max_ops
        if truncated:
            ops = ops[:max_ops]

        return {
            "operations": _serialize_value(ops),
            "count": len(ops),
            "truncated": truncated,
        }

    def get_list_databases(self) -> Dict[str, Any]:
        """List all databases with their sizes."""
        result = self._client.admin.command("listDatabases")  # type: ignore
        databases = []
        for db_info in result.get("databases", []):
            databases.append({
                "name": db_info.get("name"),
                "sizeOnDisk": db_info.get("sizeOnDisk"),
                "sizeOnDisk_mb": round(db_info.get("sizeOnDisk", 0) / (1024 * 1024), 2),
                "empty": db_info.get("empty", False),
            })
        return {
            "databases": databases,
            "totalSize_mb": round(result.get("totalSize", 0) / (1024 * 1024), 2),
            "total_count": len(databases),
        }


class BaseMongoDBTool(Tool, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, toolset: MongoDBToolset, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._toolset = toolset


class MongoDBQuery(BaseMongoDBTool):
    """Execute a find query against a MongoDB collection."""

    def __init__(self, toolset: MongoDBToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_find",
            description=(
                "Execute a find query against a MongoDB collection. "
                "Returns matching documents with optional filtering, projection, and sorting. "
                "Returns up to 200 documents by default."
            ),
            parameters={
                "collection": ToolParameter(
                    description="Name of the MongoDB collection to query",
                    type="string",
                    required=True,
                ),
                "filter": ToolParameter(
                    description=(
                        'JSON filter document for matching. Example: {"status": "error", "level": {"$gte": 3}}. '
                        "Use an empty object {} to match all documents."
                    ),
                    type="string",
                    required=True,
                ),
                "projection": ToolParameter(
                    description=(
                        'JSON projection document to include/exclude fields. Example: {"_id": 0, "name": 1, "status": 1}. '
                        "Omit to return all fields."
                    ),
                    type="string",
                    required=False,
                ),
                "sort": ToolParameter(
                    description=(
                        'JSON sort specification as an object. Example: {"timestamp": -1} for descending, {"name": 1} for ascending. '
                        "Omit for default order."
                    ),
                    type="string",
                    required=False,
                ),
                "limit": ToolParameter(
                    description="Maximum number of documents to return (default: 200, max: 200)",
                    type="integer",
                    required=False,
                ),
                "database": ToolParameter(
                    description="Database name. Uses the default database if not specified.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        collection = params["collection"]
        try:
            filter_doc = _parse_json_param(params["filter"], "filter")

            projection = None
            if params.get("projection"):
                projection = _parse_json_param(params["projection"], "projection")

            sort_spec = None
            if params.get("sort"):
                sort_obj = _parse_json_param(params["sort"], "sort")
                if isinstance(sort_obj, dict):
                    sort_spec = list(sort_obj.items())
                elif isinstance(sort_obj, list):
                    sort_spec = sort_obj

            data = self._toolset.execute_find(
                collection=collection,
                filter_doc=filter_doc,
                projection=projection,
                sort=sort_spec,
                limit=params.get("limit"),
                database=params.get("database"),
            )
            status = StructuredToolResultStatus.SUCCESS if data.get("documents") else StructuredToolResultStatus.NO_DATA
            return StructuredToolResult(
                status=status,
                data=data,
                params=params,
            )
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Query failed on collection '{collection}': {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        collection = params.get("collection", "")
        filter_str = params.get("filter", "{}")[:40]
        return f"{toolset_name_for_one_liner(self._toolset.name)}: find({collection}, {filter_str})"


class MongoDBAggregate(BaseMongoDBTool):
    """Execute an aggregation pipeline against a MongoDB collection."""

    def __init__(self, toolset: MongoDBToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_aggregate",
            description=(
                "Execute an aggregation pipeline against a MongoDB collection. "
                "Supports stages like $match, $group, $sort, $project, $unwind, $lookup, etc. "
                "In read-only mode, $out and $merge stages are not allowed."
            ),
            parameters={
                "collection": ToolParameter(
                    description="Name of the MongoDB collection to aggregate",
                    type="string",
                    required=True,
                ),
                "pipeline": ToolParameter(
                    description=(
                        "JSON array of aggregation pipeline stages. "
                        'Example: [{"$match": {"status": "error"}}, {"$group": {"_id": "$type", "count": {"$sum": 1}}}]'
                    ),
                    type="string",
                    required=True,
                ),
                "database": ToolParameter(
                    description="Database name. Uses the default database if not specified.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        collection = params["collection"]
        try:
            pipeline = _parse_json_param(params["pipeline"], "pipeline")
            if not isinstance(pipeline, list):
                raise ValueError("Pipeline must be a JSON array of stages.")

            data = self._toolset.execute_aggregate(
                collection=collection,
                pipeline=pipeline,
                database=params.get("database"),
            )
            status = StructuredToolResultStatus.SUCCESS if data.get("documents") else StructuredToolResultStatus.NO_DATA
            return StructuredToolResult(
                status=status,
                data=data,
                params=params,
            )
        except ValueError as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=str(e),
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Aggregation failed on collection '{collection}': {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        collection = params.get("collection", "")
        pipeline_str = params.get("pipeline", "[]")[:40]
        return f"{toolset_name_for_one_liner(self._toolset.name)}: aggregate({collection}, {pipeline_str})"


class MongoDBListCollections(BaseMongoDBTool):
    """List all collections in a MongoDB database."""

    def __init__(self, toolset: MongoDBToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_list_collections",
            description="List all collections in the MongoDB database.",
            parameters={
                "database": ToolParameter(
                    description="Database name. Uses the default database if not specified.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset.list_collections(
                database=params.get("database"),
            )
            status = StructuredToolResultStatus.SUCCESS if data.get("collections") else StructuredToolResultStatus.NO_DATA
            return StructuredToolResult(
                status=status,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to list collections: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        db = params.get("database", "default")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List collections in {db}"


class MongoDBCollectionSchema(BaseMongoDBTool):
    """Inspect the schema of a MongoDB collection by sampling documents."""

    def __init__(self, toolset: MongoDBToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_collection_schema",
            description=(
                "Inspect a MongoDB collection's schema by sampling documents. "
                "Returns inferred field names and types, indexes, and estimated document count."
            ),
            parameters={
                "collection": ToolParameter(
                    description="Name of the collection to inspect",
                    type="string",
                    required=True,
                ),
                "database": ToolParameter(
                    description="Database name. Uses the default database if not specified.",
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        collection = params["collection"]
        try:
            data = self._toolset.get_collection_schema(
                collection=collection,
                database=params.get("database"),
            )
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to inspect collection '{collection}': {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        collection = params.get("collection", "unknown")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: Schema of {collection}"


class MongoDBListDatabases(BaseMongoDBTool):
    """List all databases on the MongoDB server with their sizes."""

    def __init__(self, toolset: MongoDBToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_list_databases",
            description="List all databases on the MongoDB server with their sizes.",
            parameters={},
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset.get_list_databases()
            status = StructuredToolResultStatus.SUCCESS if data.get("databases") else StructuredToolResultStatus.NO_DATA
            return StructuredToolResult(
                status=status,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to list databases: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        return f"{toolset_name_for_one_liner(self._toolset.name)}: List databases"


class MongoDBServerStatus(BaseMongoDBTool):
    """Get MongoDB server status for performance diagnostics."""

    def __init__(self, toolset: MongoDBToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_server_status",
            description=(
                "Get MongoDB server status including connections, memory usage, opcounters, "
                "lock statistics, WiredTiger cache stats, and replication info. "
                "Use this to diagnose performance issues like high connection counts, "
                "memory pressure, lock contention, or replication lag."
            ),
            parameters={
                "sections": ToolParameter(
                    description=(
                        "Comma-separated list of serverStatus sections to include. "
                        "Available sections: connections, opcounters, mem, locks, globalLock, "
                        "network, wiredTiger, metrics, opLatencies, tcmalloc, flowControl. "
                        "Defaults to: connections, opcounters, mem, locks, globalLock, network, wiredTiger."
                    ),
                    type="string",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            sections = None
            if params.get("sections"):
                sections = [s.strip() for s in params["sections"].split(",")]
            data = self._toolset.get_server_status(sections=sections)
            return StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to get server status: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        sections = params.get("sections", "default")
        return f"{toolset_name_for_one_liner(self._toolset.name)}: serverStatus({sections})"


class MongoDBCurrentOp(BaseMongoDBTool):
    """Get currently running operations on the MongoDB server."""

    def __init__(self, toolset: MongoDBToolset, tool_prefix: str):
        super().__init__(
            toolset=toolset,
            name=f"{tool_prefix}_current_op",
            description=(
                "Get currently running operations on the MongoDB server. "
                "Use this to find slow queries, blocked operations, or long-running tasks. "
                "Can filter by minimum duration to find only slow operations."
            ),
            parameters={
                "min_duration_ms": ToolParameter(
                    description=(
                        "Minimum operation duration in milliseconds to filter by. "
                        "For example, 1000 returns only operations running longer than 1 second. "
                        "Omit to return all active operations."
                    ),
                    type="integer",
                    required=False,
                ),
                "active_only": ToolParameter(
                    description="If true (default), only return active operations. Set to false to include idle connections.",
                    type="boolean",
                    required=False,
                ),
            },
        )

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        try:
            data = self._toolset.get_current_op(
                min_duration_ms=params.get("min_duration_ms"),
                active_only=params.get("active_only", True),
            )
            status = StructuredToolResultStatus.SUCCESS if data.get("operations") else StructuredToolResultStatus.NO_DATA
            return StructuredToolResult(
                status=status,
                data=data,
                params=params,
            )
        except Exception as e:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Failed to get current operations: {e}",
                params=params,
            )

    def get_parameterized_one_liner(self, params: Dict) -> str:
        min_dur = params.get("min_duration_ms")
        suffix = f" (>{min_dur}ms)" if min_dur else ""
        return f"{toolset_name_for_one_liner(self._toolset.name)}: currentOp{suffix}"
