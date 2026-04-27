import logging
from enum import Enum
from typing import Any, ClassVar, List

from pydantic import Field, model_validator

from holmes.plugins.toolsets.datadog.datadog_api import DatadogBaseConfig
from holmes.plugins.toolsets.logging_utils.logging_api import DEFAULT_LOG_LIMIT

logger = logging.getLogger(__name__)

# Constants for RDS toolset
DEFAULT_TIME_SPAN_SECONDS = 3600
DEFAULT_TOP_INSTANCES = 10

# Constants for general toolset
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB

# Default maximum number of metric data points returned by the metrics toolset
# when the user doesn't specify one explicitly.
DEFAULT_METRICS_LIMIT = 100


class DataDogStorageTier(str, Enum):
    """Storage tier enum for Datadog logs."""

    INDEXES = "indexes"
    ONLINE_ARCHIVES = "online-archives"
    FLEX = "flex"


# Default Datadog log storage tier when the user doesn't specify one.
DEFAULT_STORAGE_TIER = DataDogStorageTier.INDEXES


class DatadogMetricsConfig(DatadogBaseConfig):
    """Configuration for Datadog metrics toolset."""

    default_limit: int = Field(
        default=DEFAULT_METRICS_LIMIT,
        description="Default maximum number of results to return when a limit is not explicitly provided",
    )


class DatadogTracesConfig(DatadogBaseConfig):
    """Configuration for Datadog traces toolset."""

    # Hide list-typed advanced fields from the frontend form and example YAML.
    # The runtime still accepts them via raw YAML for users who need to override.
    _hidden_fields: ClassVar[List[str]] = ["indexes"]

    indexes: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Datadog trace index patterns to search. Use ['*'] for all indexes",
        examples=[["*"], ["main"], ["trace-*"]],
    )


class DatadogLogsConfig(DatadogBaseConfig):
    """Configuration for Datadog logs toolset."""

    # Hide the `indexes` list from the frontend form and example YAML
    # because complex list types don't render as form inputs. Runtime still
    # accepts it via raw YAML for advanced users.
    _hidden_fields: ClassVar[List[str]] = ["indexes"]

    indexes: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Datadog log index patterns to search. Use ['*'] for all indexes",
        examples=[["*"], ["main"], ["logs-*"]],
    )
    storage_tier: DataDogStorageTier = Field(
        default=DEFAULT_STORAGE_TIER,
        title="Storage Tier",
        description=(
            "Which Datadog log storage tier to search: 'indexes' for recent hot "
            "logs (default), 'flex' for medium-retention, or 'online-archives' "
            "for cold long-term storage."
        ),
        examples=["indexes", "flex", "online-archives"],
    )

    compact_logs: bool = Field(
        default=True,
        description="Whether to compact log entries to reduce response size and token usage",
    )
    default_limit: int = Field(
        default=DEFAULT_LOG_LIMIT,
        description="Default maximum number of log events to return when a limit is not explicitly provided",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_storage_tiers_to_singular(cls, data: Any) -> Any:
        """
        Back-compat for the deprecated list-valued `storage_tiers` field.

        Datadog's log search API only accepts one storage tier per request, so the
        old `storage_tiers: list[...]` schema was misleading — the runtime only
        consumed the last element. We now expose a scalar `storage_tier` field.
        If a legacy config supplies `storage_tiers`, take the last element (preserves
        the previous runtime behaviour) and log a deprecation warning.
        """
        if not isinstance(data, dict):
            return data
        if "storage_tiers" in data and "storage_tier" not in data:
            legacy = data.pop("storage_tiers")
            logger.warning(
                "DatadogLogsConfig: 'storage_tiers' is deprecated — use 'storage_tier' "
                "(singular). Datadog's log API queries a single tier per request."
            )
            if isinstance(legacy, list) and legacy:
                data["storage_tier"] = legacy[-1]
            elif isinstance(legacy, str):
                data["storage_tier"] = legacy
            # If legacy is an empty list or unexpected type, fall through so the
            # field default ('indexes') applies.
        return data


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
