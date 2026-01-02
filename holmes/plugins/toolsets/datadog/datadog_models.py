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

    default_limit: int = DEFAULT_LOG_LIMIT


class DatadogTracesConfig(DatadogBaseConfig):
    """Configuration for Datadog traces toolset."""

    indexes: list[str] = ["*"]


class DatadogLogsConfig(DatadogBaseConfig):
    """Configuration for Datadog logs toolset."""

    indexes: list[str] = ["*"]
    # TODO storage tier just works with first element. need to add support for multi stoarge tiers.
    storage_tiers: list[DataDogStorageTier] = Field(
        default_factory=lambda: [DataDogStorageTier.INDEXES], min_length=1
    )

    compact_logs: bool = True
    default_limit: int = DEFAULT_LOG_LIMIT


class DatadogGeneralConfig(DatadogBaseConfig):
    """Configuration for general-purpose Datadog toolset."""

    max_response_size: int = MAX_RESPONSE_SIZE
    allow_custom_endpoints: bool = (
        False  # If True, allows endpoints not in whitelist (still filtered for safety)
    )
