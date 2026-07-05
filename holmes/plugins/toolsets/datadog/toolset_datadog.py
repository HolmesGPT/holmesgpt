import logging
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from pydantic import ConfigDict, Field

from holmes.core.tools import (
    CallablePrerequisite,
    Tool,
    Toolset,
    ToolsetTag,
)
from holmes.plugins.toolsets.datadog.datadog_api import DatadogBaseConfig
from holmes.plugins.toolsets.datadog.datadog_models import (
    DEFAULT_METRICS_LIMIT,
    MAX_RESPONSE_SIZE,
    DataDogStorageTier,
    DEFAULT_STORAGE_TIER,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_general import (
    DatadogGeneralToolset,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_logs import DatadogLogsToolset
from holmes.plugins.toolsets.datadog.toolset_datadog_metrics import (
    DatadogMetricsToolset,
)
from holmes.plugins.toolsets.datadog.toolset_datadog_traces import (
    DatadogTracesToolset,
)
from holmes.plugins.toolsets.logging_utils.logging_api import DEFAULT_LOG_LIMIT

logger = logging.getLogger(__name__)


class DatadogConfig(DatadogBaseConfig):
    """Unified configuration for the consolidated Datadog toolset.

    Carries the shared credentials (inherited from DatadogBaseConfig) plus the
    per-signal optional tuning fields, fanned out to the individual logs/metrics/
    traces/general sub-toolsets at prerequisite time. The `default_limit` field
    name is de-collided here (`logs_default_limit` vs `metrics_default_limit`)
    because it means different things for logs and metrics.
    """

    # List-typed fields don't render as form inputs; hide them from the frontend
    # form and example YAML while still accepting them via raw YAML.
    _hidden_fields: ClassVar[List[str]] = ["logs_indexes", "traces_indexes"]

    # Logs
    logs_default_limit: int = Field(
        default=DEFAULT_LOG_LIMIT,
        description="Default maximum number of log events to return when a limit is not explicitly provided",
    )
    storage_tier: DataDogStorageTier = Field(
        default=DEFAULT_STORAGE_TIER,
        title="Log Storage Tier",
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
    logs_indexes: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Datadog log index patterns to search. Use ['*'] for all indexes",
        examples=[["*"], ["main"], ["logs-*"]],
    )

    # Metrics
    metrics_default_limit: int = Field(
        default=DEFAULT_METRICS_LIMIT,
        description="Default maximum number of metric results to return when a limit is not explicitly provided",
    )

    # Traces
    traces_indexes: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Datadog trace index patterns to search. Use ['*'] for all indexes",
        examples=[["*"], ["main"], ["trace-*"]],
    )

    # General API access
    max_response_size: int = Field(
        default=MAX_RESPONSE_SIZE,
        description="Maximum size (in bytes) of API responses returned by the general Datadog API tools",
    )
    allow_custom_endpoints: bool = Field(
        default=False,
        description="If true, allows the general Datadog API tools to call endpoints not in the whitelist (still filtered for safety/read-only)",
    )


# Ordered so the merged llm_instructions and any duplicate-name resolution are
# deterministic. Each entry maps the unified config to the sub-toolset's own
# (possibly differently named) config fields.
def _sub_configs(config: DatadogConfig) -> List[Tuple[str, Type[Toolset], Dict[str, Any]]]:
    creds: Dict[str, Any] = {
        "api_key": config.api_key,
        "app_key": config.app_key,
        "api_url": str(config.api_url),
        "timeout_seconds": config.timeout_seconds,
    }
    return [
        (
            "logs",
            DatadogLogsToolset,
            {
                **creds,
                "default_limit": config.logs_default_limit,
                "storage_tier": config.storage_tier,
                "compact_logs": config.compact_logs,
                "indexes": config.logs_indexes,
            },
        ),
        (
            "metrics",
            DatadogMetricsToolset,
            {**creds, "default_limit": config.metrics_default_limit},
        ),
        (
            "traces",
            DatadogTracesToolset,
            {**creds, "indexes": config.traces_indexes},
        ),
        (
            "general",
            DatadogGeneralToolset,
            {
                **creds,
                "max_response_size": config.max_response_size,
                "allow_custom_endpoints": config.allow_custom_endpoints,
            },
        ),
    ]


class DatadogToolset(Toolset):
    """Umbrella toolset that exposes all Datadog capabilities (logs, metrics,
    APM traces, and general API access) from a single shared credential config.

    It composes the four specialized Datadog sub-toolsets: it builds each one,
    runs its own prerequisite/health check with the shared credentials, and
    reuses its tools directly. This mirrors the composite pattern used by
    ConfluenceToolset and keeps each sub-toolset's tool logic, health checks,
    and instructions untouched.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config_classes: ClassVar[list[Type[DatadogConfig]]] = [DatadogConfig]

    def __init__(self):
        super().__init__(
            name="datadog",
            description=(
                "Unified Datadog toolset for querying logs, metrics, APM traces, "
                "and general read-only Datadog APIs (monitors, dashboards, SLOs, "
                "incidents, synthetics, hosts, and more)"
            ),
            docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            icon_url="https://raw.githubusercontent.com/gilbarbara/logos/de2c1f96ff6e74ea7ea979b43202e8d4b863c655/logos/datadog.svg",
            prerequisites=[CallablePrerequisite(callable=self.prerequisites_callable)],
            tools=[],
            tags=[ToolsetTag.CORE],
        )
        # Sub-toolset instances are held so their `dd_config` stays alive and the
        # reused tools' `.toolset` back-references remain valid across calls.
        self._subtoolsets: List[Toolset] = []

    def prerequisites_callable(self, config: dict[str, Any]) -> Tuple[bool, str]:
        if not config:
            return (
                False,
                "Missing config for api_key, app_key, or api_url. For details: https://holmesgpt.dev/data-sources/builtin-toolsets/datadog/",
            )

        try:
            dd_config = DatadogConfig(**config)
        except Exception as e:
            logging.exception("Failed to set up Datadog toolset")
            return False, f"Invalid Datadog configuration: {e}"

        subtoolsets: List[Toolset] = []
        tools: List[Tool] = []
        instructions: List[str] = []
        healthy: List[str] = []
        failures: List[str] = []

        for area, toolset_cls, sub_config in _sub_configs(dd_config):
            sub = toolset_cls()
            # Forward approval gating so it applies to the reused sub-toolset tools
            # (approval checks read `tool.toolset.approval_required_tools`).
            sub.approval_required_tools = self.approval_required_tools
            try:
                ok, msg = sub.prerequisites_callable(sub_config)
            except Exception as e:
                ok, msg = False, str(e)

            if ok:
                healthy.append(area)
            else:
                failures.append(f"[{area}] {msg or 'prerequisite check failed'}".strip())

            subtoolsets.append(sub)
            tools.extend(sub.tools)
            if sub.llm_instructions:
                instructions.append(f"### Datadog {area.capitalize()}\n\n{sub.llm_instructions}")

        self._subtoolsets = subtoolsets
        self.tools = tools
        if instructions:
            self.llm_instructions = "\n\n".join(instructions)

        return self._aggregate(healthy, failures)

    def _aggregate(
        self, healthy: List[str], failures: List[str]
    ) -> Tuple[bool, str]:
        """Tolerant aggregation: enable if at least one area is healthy, and
        surface every failing area's reason. A single Datadog credential may
        legitimately have access to some signals (e.g. logs) but not others."""
        if not healthy:
            return False, "\n".join(failures) or "No Datadog areas could be configured"
        if failures:
            logger.warning(
                "datadog: %d/%d area(s) healthy. Failed: %s",
                len(healthy),
                len(healthy) + len(failures),
                failures,
            )
            return True, "Some Datadog areas are unavailable: " + " | ".join(failures)
        return True, ""
