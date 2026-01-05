import json
import logging
from typing import Any, Optional, cast
from urllib.parse import urljoin

import requests  # type: ignore
from pydantic import BaseModel

from holmes.core.tools import Toolset


class OpenSearchLoggingLabelsConfig(BaseModel):
    pod: str = "kubernetes.pod_name"
    namespace: str = "kubernetes.namespace_name"
    timestamp: str = "@timestamp"
    message: str = "message"


class BaseOpenSearchConfig(BaseModel):
    opensearch_url: str
    index_pattern: str
    opensearch_auth_header: Optional[str] = None
    # Setting to None will disable the cache
    fields_ttl_seconds: Optional[int] = 14400  # 4 hours


class OpenSearchLoggingConfig(BaseOpenSearchConfig):
    labels: OpenSearchLoggingLabelsConfig = OpenSearchLoggingLabelsConfig()


def add_auth_header(auth_header: Optional[str]) -> dict[str, Any]:
    results = {}
    if auth_header:
        results["Authorization"] = auth_header
    return results


def get_search_url(config: BaseOpenSearchConfig) -> str:
    return urljoin(config.opensearch_url, f"/{config.index_pattern}/_search")


def opensearch_health_check(config: BaseOpenSearchConfig) -> tuple[bool, str]:
    url = get_search_url(config)
    try:
        headers = {"Content-Type": "application/json"}
        headers.update(add_auth_header(config.opensearch_auth_header))
        health_response = requests.get(
            url=url,
            verify=True,
            data=json.dumps({"size": 1}),
            headers=headers,
        )
        health_response.raise_for_status()
        return True, ""
    except Exception as e:
        logging.info("Failed to initialize opensearch toolset", exc_info=True)
        return False, f"Failed to initialize opensearch toolset. url={url}. {str(e)}"


def format_log_to_json(log_line: Any) -> str:
    try:
        return json.dumps(log_line)
    except Exception:
        # Handle potential serialization errors (e.g., non-serializable objects)
        return str(log_line)


def format_logs(
    logs: list[dict[str, Any]],
    config: OpenSearchLoggingConfig,
) -> str:
    if not logs or not isinstance(logs, list):
        return ""

    # Get field names from config or use defaults
    message_field = config.labels.message

    formatted_lines = []

    for hit in logs:
        # Ensure hit is a dictionary and has _source
        if not isinstance(hit, dict):
            formatted_lines.append(format_log_to_json(hit))
            continue
        source = hit.get("_source")
        if not isinstance(source, dict):
            formatted_lines.append(format_log_to_json(hit))
            continue

        message = source.get(message_field, None)

        if message and not isinstance(message, str):
            message = str(message)  # Convert non-strings

        if message:
            formatted_lines.append(message)
        else:
            # fallback displaying the logs line as-is
            formatted_lines.append(format_log_to_json(hit))

    return "\n".join(formatted_lines)


def build_query(
    config: OpenSearchLoggingConfig,
    namespace: str,
    pod_name: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    match: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    size = limit if limit else 5000

    pod_field = config.labels.pod
    namespace_field = config.labels.namespace
    timestamp_field = config.labels.timestamp
    message_field = config.labels.message

    must_constraints: list[dict] = [
        {"term": {f"{pod_field}.keyword": pod_name}},
        {"term": {f"{namespace_field}.keyword": namespace}},
    ]

    query = {
        "size": size,
        "sort": [{timestamp_field: {"order": "asc"}}],
        "query": {"bool": {"must": must_constraints}},
    }

    # Add timestamp range if provided
    if start_time or end_time:
        range_query: dict = {"range": {timestamp_field: {}}}
        if start_time:
            range_query["range"][timestamp_field]["gte"] = start_time
        if end_time:
            range_query["range"][timestamp_field]["lte"] = end_time

        must_constraints.append(range_query)

    # Add message filter if provided
    if match:
        must_constraints.append({"match_phrase": {message_field: match}})

    return query


class BaseOpenSearchToolset(Toolset):
    def get_example_config(self) -> dict[str, Any]:
        example_config = BaseOpenSearchConfig(
            opensearch_url="YOUR OPENSEARCH URL",
            index_pattern="YOUR OPENSEARCH INDEX NAME",
            opensearch_auth_header="YOUR OPENSEARCH AUTH HEADER (Optional)",
        )
        return example_config.model_dump()

    def prerequisites_callable(self, config: dict[str, Any]) -> tuple[bool, str]:
        if not config:
            return False, "Missing opensearch traces URL. Check your config"
        else:
            self.config = BaseOpenSearchConfig(**config)
            return opensearch_health_check(self.config)

    @property
    def opensearch_config(self) -> BaseOpenSearchConfig:
        return cast(BaseOpenSearchConfig, self.config)
