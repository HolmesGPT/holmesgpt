import logging
from typing import Any, Dict, Optional, Tuple, Set

import requests  # type: ignore
from requests import RequestException  # type: ignore
from urllib.parse import urljoin

from holmes.core.tools import (
    CallablePrerequisite,
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolsetTag,
)
from holmes.plugins.toolsets.logging_utils.logging_api import (
    BasePodLoggingToolset,
    FetchPodLogsParams,
    LoggingCapability,
    PodLoggingTool,
    process_time_parameters,
    DEFAULT_TIME_SPAN_SECONDS,
)
from holmes.plugins.toolsets.opensearch.opensearch_utils import (
    OpenSearchLoggingConfig,
    add_auth_header,
    build_query,
    format_logs,
    opensearch_health_check,
)

LOGS_FIELDS_CACHE_KEY = "cached_logs_fields"


class OpenSearchLogsToolset(BasePodLoggingToolset):
    """Implementation of the unified logging API for OpenSearch logs"""

    @property
    def supported_capabilities(self) -> Set[LoggingCapability]:
        """OpenSearch only supports phrase matching, not regex or exclude filters"""
        return set()  # No regex support, no exclude filter

    def __init__(self):
        super().__init__(
            name="opensearch/logs",
            description="OpenSearch integration to fetch logs",
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/opensearch-logs/",
            icon_url="https://opensearch.org/wp-content/uploads/2025/01/opensearch_mark_default.png",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],  # Initialize with empty tools first
            tags=[
                ToolsetTag.CORE,
            ],
        )
        # Now that parent is initialized and self.name exists, create the tool
        self.tools = [PodLoggingTool(self)]

    def get_example_config(self) -> Dict[str, Any]:
        example_config = OpenSearchLoggingConfig(
            opensearch_url="YOUR OPENSEARCH LOGS URL",
            index_pattern="YOUR OPENSEARCH LOGS INDEX NAME",
            opensearch_auth_header="YOUR OPENSEARCH LOGS AUTH HEADER (Optional)",
        )
        return example_config.model_dump()

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return False, "Missing OpenSearch configuration. Check your config."

        self.config = OpenSearchLoggingConfig(**config)

        return opensearch_health_check(self.config)

    @property
    def opensearch_config(self) -> Optional[OpenSearchLoggingConfig]:
        return self.config

    def logger_name(self) -> str:
        return "OpenSearch"

    def fetch_pod_logs(self, params: FetchPodLogsParams) -> StructuredToolResult:
        if not self.opensearch_config:
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Missing OpenSearch configuration",
                params=params.model_dump(),
            )

        try:
            start_time = None
            end_time = None
            if params.start_time or params.end_time:
                start_time, end_time = process_time_parameters(
                    params.start_time, params.end_time, DEFAULT_TIME_SPAN_SECONDS
                )

            query = build_query(
                config=self.opensearch_config,
                namespace=params.namespace,
                pod_name=params.pod_name,
                start_time=start_time,
                end_time=end_time,
                match=params.filter,
                limit=params.limit,
            )

            headers = {"Content-Type": "application/json"}
            headers.update(
                add_auth_header(self.opensearch_config.opensearch_auth_header)
            )

            url = urljoin(
                self.opensearch_config.opensearch_url,
                f"/{self.opensearch_config.index_pattern}/_search",
            )
            logs_response = requests.post(
                url=url,
                timeout=180,
                verify=True,
                json=query,
                headers=headers,
            )

            if logs_response.status_code == 200:
                response = logs_response.json()
                logs = format_logs(
                    logs=response.get("hits", {}).get("hits", []),
                    config=self.opensearch_config,
                )
                return StructuredToolResult(
                    status=StructuredToolResultStatus.SUCCESS,
                    data=logs,
                    params=params.model_dump(),
                )
            else:
                return StructuredToolResult(
                    status=StructuredToolResultStatus.ERROR,
                    return_code=logs_response.status_code,
                    error=logs_response.text,
                    params=params.model_dump(),
                )

        except requests.Timeout:
            logging.warning("Timeout while fetching OpenSearch logs", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error="Request timed out while fetching OpenSearch logs",
                params=params.model_dump(),
            )
        except RequestException as e:
            logging.warning("Failed to fetch OpenSearch logs", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Network error while fetching OpenSearch logs: {str(e)}",
                params=params.model_dump(),
            )
        except Exception as e:
            logging.warning("Failed to process OpenSearch logs", exc_info=True)
            return StructuredToolResult(
                status=StructuredToolResultStatus.ERROR,
                error=f"Unexpected error: {str(e)}",
                params=params.model_dump(),
            )
