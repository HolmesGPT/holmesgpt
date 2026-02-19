from enum import Enum

from pydantic import Field

from holmes.plugins.toolsets.datadog.datadog_api import DatadogBaseConfig
from holmes.plugins.toolsets.logging_utils.logging_api import DEFAULT_LOG_LIMIT

# Constants for RDS toolset
DEFAULT_TIME_SPAN_SECONDS = 3600
DEFAULT_TOP_INSTANCES = 10

# Constants for general toolset
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB


class DataDogStorageTier(str, Enum):
    """Storage tier enum for Datadog logs."""

    INDEXES = "indexes"
    ONLINE_ARCHIVES = "online-archives"
    FLEX = "flex"


# Constants for logs toolset
DEFAULT_STORAGE_TIERS = [DataDogStorageTier.INDEXES]


class DatadogMetricsConfig(DatadogBaseConfig):
    """Configuration for Datadog metrics toolset."""

    default_limit: int = Field(
        default=DEFAULT_LOG_LIMIT,
        description="Default maximum number of results to return when a limit is not explicitly provided",
    )


class DatadogTracesConfig(DatadogBaseConfig):
    """Configuration for Datadog traces toolset."""

    indexes: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Datadog trace index patterns to search. Use ['*'] for all indexes",
        examples=[["*"], ["main"], ["trace-*"]],
    )


class DatadogLogsConfig(DatadogBaseConfig):
    """Configuration for Datadog logs toolset."""

    indexes: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Datadog log index patterns to search. Use ['*'] for all indexes",
        examples=[["*"], ["main"], ["logs-*"]],
    )
    # TODO storage tier just works with first element. need to add support for multi stoarge tiers.
    storage_tiers: list[DataDogStorageTier] = Field(
        default_factory=lambda: [DataDogStorageTier.INDEXES], min_length=1
    )

    compact_logs: bool = Field(
        default=True,
        description="Whether to compact log entries to reduce response size and token usage",
    )
    default_limit: int = Field(
        default=DEFAULT_LOG_LIMIT,
        description="Default maximum number of log events to return when a limit is not explicitly provided",
    )


class DatadogGeneralConfig(DatadogBaseConfig):
    """Configuration for general-purpose Datadog toolset."""

    max_response_size: int = Field(
        default=MAX_RESPONSE_SIZE,
        description="Maximum size (in bytes) of API responses returned by the toolset",
    )
    allow_custom_endpoints: bool = Field(
        default=False,
        description="If true, allows calling endpoints not in the whitelist (still filtered for safety/read-only)",
    )
