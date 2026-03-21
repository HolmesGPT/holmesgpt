import logging
import os
import os.path
from typing import Any, List, Optional, Union

import yaml  # type: ignore
from benedict import benedict
from pydantic import FilePath, ValidationError

import holmes.utils.env as env_utils
from holmes.common.env_vars import (
    DISABLE_PROMETHEUS_TOOLSET,
    USE_LEGACY_KUBERNETES_LOGS,
)
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import Toolset, ToolsetType, ToolsetYamlFromConfig, YAMLToolset

TOOLSETS_PLUGIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "plugins", "toolsets")
)

# Mapping of deprecated toolset names to their new names
DEPRECATED_TOOLSET_NAMES: dict[str, str] = {
    "coralogix/logs": "coralogix",
}


def handle_deprecated_toolset_name(
    toolset_name: str, builtin_toolset_names: list[str]
) -> str:
    if toolset_name in DEPRECATED_TOOLSET_NAMES:
        new_name = DEPRECATED_TOOLSET_NAMES[toolset_name]
        if new_name in builtin_toolset_names:
            logging.warning(
                f"The toolset name '{toolset_name}' is deprecated. "
                f"Please use '{new_name}' instead. "
                "The old name will continue to work but may be removed in a future version."
            )
            return new_name
    return toolset_name


class ToolsetRegistry:
    """Discovers, loads, merges toolsets and decides which are enabled.

    The registry owns the full pipeline from raw config to a resolved
    ``dict[str, Toolset]`` with ``enabled`` already set on each toolset.
    It does NOT run prerequisites — that is the manager's job.
    """

    def __init__(
        self,
        toolsets_config: Optional[dict[str, dict[str, Any]]] = None,
        custom_toolset_paths: Optional[List[FilePath]] = None,
        additional_toolsets: Optional[List[Toolset]] = None,
        custom_runbook_catalogs: Optional[List[Union[str, FilePath]]] = None,
    ):
        self.toolsets_config = toolsets_config or {}
        self.custom_toolset_paths = custom_toolset_paths or []
        self.additional_toolsets = additional_toolsets or []
        self.custom_runbook_catalogs = custom_runbook_catalogs

    def get_all_toolsets(
        self,
        dal: Optional[SupabaseDal] = None,
        auto_enable: bool = False,
        tag_filter: Optional[list] = None,
    ) -> dict[str, Toolset]:
        """Return all toolsets with enabled state resolved.

        Pipeline:
        1. _discover_builtin_toolsets() — YAML files + Python classes
        2. _apply_config_overrides() — user config merged onto builtins
        3. _apply_custom_toolsets() — custom toolset files merged in
        4. _apply_additional_toolsets() — programmatic toolsets added
        5. For each toolset: toolset.enabled = should_enable_toolset(...)
        6. Filter by tag_filter if provided
        """
        # Step 1: Discover builtins
        additional_search_paths = None
        if self.custom_runbook_catalogs:
            additional_search_paths = [
                os.path.dirname(os.path.abspath(str(catalog_path)))
                for catalog_path in self.custom_runbook_catalogs
            ]

        builtin_toolsets = _discover_builtin_toolsets(dal, additional_search_paths)
        toolsets_by_name: dict[str, Toolset] = {
            toolset.name: toolset for toolset in builtin_toolsets
        }
        builtin_toolset_names = list(toolsets_by_name.keys())

        # Step 2: Apply config overrides
        if self.toolsets_config:
            toolsets_from_config = self._apply_config_overrides(
                self.toolsets_config, builtin_toolset_names, dal
            )
            if toolsets_from_config:
                _merge_onto(toolsets_from_config, toolsets_by_name)

        # Step 3: Apply custom toolsets from file paths
        custom_toolsets = self._load_custom_toolsets(builtin_toolset_names)
        _merge_onto(custom_toolsets, toolsets_by_name)

        # Step 4: Add additional Python toolsets passed programmatically
        if self.additional_toolsets:
            for toolset in self.additional_toolsets:
                toolset.type = ToolsetType.CUSTOM_YAML
                toolsets_by_name[toolset.name] = toolset

        # Step 5: Decide enabled state for each toolset
        # Build a normalized set of config keys so deprecated names
        # (e.g. "coralogix/logs") match the canonical name ("coralogix").
        normalized_config_keys = {
            handle_deprecated_toolset_name(k, builtin_toolset_names)
            for k in self.toolsets_config
        }
        for name, toolset in toolsets_by_name.items():
            explicitly_configured = name in self.toolsets_config or name in normalized_config_keys
            toolset.enabled = self.should_enable_toolset(
                toolset, explicitly_configured, auto_enable
            )

        # Step 6: Filter by tags
        if tag_filter is not None:
            toolsets_by_name = {
                name: toolset
                for name, toolset in toolsets_by_name.items()
                if any(tag in tag_filter for tag in toolset.tags)
            }

        return toolsets_by_name

    def should_enable_toolset(
        self,
        toolset: Toolset,
        explicitly_configured: bool,
        auto_enable: bool,
    ) -> bool:
        """Single source of truth for whether a toolset should be enabled.

        This preserves the exact existing behavior — this is a refactoring,
        not a behavior change.

        Args:
            toolset: The toolset to evaluate.
            explicitly_configured: True if the user named this toolset in their
                config dict (including MCP servers merged into it).
            auto_enable: True if auto-enabling all toolsets that can work without config.
        """
        # Explicitly configured → respect the enabled flag from config.
        # This preserves current behavior: ToolsetYamlFromConfig defaults
        # enabled=True, but override_with uses exclude_unset=True, so writing
        # just `kubernetes/logs: {}` does NOT set enabled on the builtin.
        # The enabled flag only flows through if the user explicitly wrote
        # `enabled: true/false` in their config or if _apply_config_overrides
        # set it (for custom toolsets).
        if explicitly_configured:
            return toolset.enabled

        # Custom/MCP/HTTP/DATABASE toolsets default to enabled, but respect
        # an explicit ``enabled: false`` set during file loading.
        if toolset.type in (
            ToolsetType.CUSTOM_YAML,
            ToolsetType.MCP,
            ToolsetType.HTTP,
            ToolsetType.DATABASE,
            ToolsetType.MONGODB,
        ):
            return toolset.enabled

        # Built-in with is_default=True → always enable.
        # Preserves pre-refactor behavior: should_auto_enable() returned True
        # for ``self.enabled or self.is_default`` before any other checks.
        if toolset.is_default:
            return True

        # Built-in + auto_enable → enable if config requirements are met
        if auto_enable:
            if not toolset.missing_config:
                return True
            else:
                logging.debug(
                    f"Toolset '{toolset.name}' not auto-enabled: "
                    f"requires configuration that was not provided"
                )
                return False

        return False

    def _apply_config_overrides(
        self,
        toolsets: dict[str, dict[str, Any]],
        builtin_toolset_names: list[str],
        dal: Optional[SupabaseDal] = None,
    ) -> List[Toolset]:
        if toolsets is None:
            logging.debug("No toolsets configured, skipping loading toolsets")
            return []

        builtin_toolsets_dict: dict[str, dict[str, Any]] = {}
        custom_toolsets_dict: dict[str, dict[str, Any]] = {}

        for toolset_name, toolset_config in toolsets.items():
            toolset_name = handle_deprecated_toolset_name(
                toolset_name, builtin_toolset_names
            )

            if toolset_name in builtin_toolset_names:
                # Direct reference to builtin toolset by name
                builtin_toolsets_dict[toolset_name] = toolset_config
            else:
                # Custom toolset (including HTTP, DATABASE, MCP, etc.)
                if toolset_config.get("type") is None:
                    toolset_config["type"] = ToolsetType.CUSTOM_YAML.value
                # custom toolsets defaults to enabled when not explicitly disabled
                if toolset_config.get("enabled", True) is False:
                    toolset_config["enabled"] = False
                else:
                    toolset_config["enabled"] = True
                custom_toolsets_dict[toolset_name] = toolset_config

        # built-in toolsets and built-in MCP servers in the config can override the existing fields of built-in toolsets
        builtin_toolsets = _parse_toolset_config(
            builtin_toolsets_dict, strict_check=False
        )

        # custom toolsets or MCP servers are expected to defined required fields
        custom_toolsets = _parse_toolset_config(
            toolsets=custom_toolsets_dict, strict_check=True
        )

        return builtin_toolsets + custom_toolsets

    def _load_custom_toolsets(
        self, builtin_toolset_names: list[str]
    ) -> list[Toolset]:
        """Load toolsets from custom toolset file paths."""
        if not self.custom_toolset_paths:
            logging.debug(
                "No custom toolsets configured, skipping loading custom toolsets"
            )
            return []

        return self._load_toolsets_from_paths(
            self.custom_toolset_paths, builtin_toolset_names
        )

    def _load_toolsets_from_paths(
        self,
        toolset_paths: List[FilePath],
        builtin_toolset_names: list[str],
        check_conflict_default: bool = False,
    ) -> List[Toolset]:
        if not toolset_paths:
            logging.debug("No toolsets configured, skipping loading toolsets")
            return []

        loaded_custom_toolsets: List[Toolset] = []
        for toolset_path in toolset_paths:
            if not os.path.isfile(toolset_path):
                raise FileNotFoundError(f"toolset file {toolset_path} does not exist")

            try:
                parsed_yaml = benedict(toolset_path)
            except Exception as e:
                raise ValueError(
                    f"Failed to load toolsets from {toolset_path}, error: {e}"
                ) from e
            toolsets_config: dict[str, dict[str, Any]] = parsed_yaml.get(
                "toolsets", {}
            )
            mcp_config: dict[str, dict[str, Any]] = parsed_yaml.get(
                "mcp_servers", {}
            )

            for server_config in mcp_config.values():
                server_config["type"] = ToolsetType.MCP.value

            for toolset_config in toolsets_config.values():
                toolset_config["path"] = toolset_path

            toolsets_config.update(mcp_config)

            if not toolsets_config:
                raise ValueError(
                    f"No 'toolsets' or 'mcp_servers' key found in: {toolset_path}"
                )

            toolsets_from_config = self._apply_config_overrides(
                toolsets_config, builtin_toolset_names
            )
            if check_conflict_default:
                for toolset in toolsets_from_config:
                    if toolset.name in builtin_toolset_names:
                        raise Exception(
                            f"Toolset {toolset.name} is already defined in the built-in toolsets. "
                            "Please rename the custom toolset or remove it from the custom toolsets configuration."
                        )

            loaded_custom_toolsets.extend(toolsets_from_config)

        return loaded_custom_toolsets


# ---------------------------------------------------------------------------
# Module-level helpers (previously in holmes/plugins/toolsets/__init__.py)
# ---------------------------------------------------------------------------


def _discover_builtin_toolsets(
    dal: Optional[SupabaseDal] = None,
    additional_search_paths: Optional[List[str]] = None,
) -> List[Toolset]:
    all_toolsets: List[Toolset] = []
    logging.debug(f"loading toolsets from {TOOLSETS_PLUGIN_DIR}")

    # Handle YAML toolsets
    for filename in os.listdir(TOOLSETS_PLUGIN_DIR):
        if not filename.endswith(".yaml"):
            continue

        if filename == "kubernetes_logs.yaml" and not USE_LEGACY_KUBERNETES_LOGS:
            continue

        path = os.path.join(TOOLSETS_PLUGIN_DIR, filename)
        toolsets_from_file = _load_toolsets_from_file(path, strict_check=True)
        all_toolsets.extend(toolsets_from_file)

    all_toolsets.extend(
        _discover_python_toolsets(
            dal=dal, additional_search_paths=additional_search_paths
        )
    )  # type: ignore

    # disable built-in toolsets by default, and the user can enable them explicitly in config.
    for toolset in all_toolsets:
        toolset.type = ToolsetType.BUILTIN
        # don't expose built-in toolsets path
        toolset.path = None

    return all_toolsets  # type: ignore


def _discover_python_toolsets(
    dal: Optional[SupabaseDal],
    additional_search_paths: Optional[List[str]] = None,
) -> List[Toolset]:
    from holmes.plugins.toolsets.atlas_mongodb.mongodb_atlas import MongoDBAtlasToolset
    from holmes.plugins.toolsets.azure_sql.azure_sql_toolset import AzureSQLToolset
    from holmes.plugins.toolsets.bash.bash_toolset import BashExecutorToolset
    from holmes.plugins.toolsets.confluence.confluence import ConfluenceToolset
    from holmes.plugins.toolsets.connectivity_check import ConnectivityCheckToolset
    from holmes.plugins.toolsets.coralogix.toolset_coralogix import CoralogixToolset
    from holmes.plugins.toolsets.database.database import DatabaseToolset
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
    from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
        ElasticsearchClusterToolset,
        ElasticsearchDataToolset,
    )
    from holmes.plugins.toolsets.elasticsearch.opensearch_query_assist import (
        OpenSearchQueryAssistToolset,
    )
    from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import (
        GrafanaLokiToolset,
    )
    from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaToolset
    from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import (
        GrafanaTempoToolset,
    )
    from holmes.plugins.toolsets.internet.internet import InternetToolset
    from holmes.plugins.toolsets.internet.notion import NotionToolset
    from holmes.plugins.toolsets.investigator.core_investigation import (
        CoreInvestigationToolset,
    )
    from holmes.plugins.toolsets.kafka import KafkaToolset
    from holmes.plugins.toolsets.kubectl_run.kubectl_run_toolset import (
        KubectlRunToolset,
    )
    from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset
    from holmes.plugins.toolsets.mongodb.mongodb import MongoDBToolset
    from holmes.plugins.toolsets.newrelic.newrelic import NewRelicToolset
    from holmes.plugins.toolsets.rabbitmq.toolset_rabbitmq import RabbitMQToolset
    from holmes.plugins.toolsets.robusta.robusta import RobustaToolset
    from holmes.plugins.toolsets.runbook.runbook_fetcher import RunbookToolset
    from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
        ServiceNowTablesToolset,
    )

    logging.debug("loading python toolsets")
    toolsets: list[Toolset] = [
        CoreInvestigationToolset(),  # Load first for higher priority
        InternetToolset(),
        ConnectivityCheckToolset(),
        RobustaToolset(dal),
        GrafanaLokiToolset(),
        GrafanaTempoToolset(),
        NewRelicToolset(),
        GrafanaToolset(),
        NotionToolset(),
        KafkaToolset(),
        DatadogLogsToolset(),
        DatadogGeneralToolset(),
        DatadogMetricsToolset(),
        DatadogTracesToolset(),
        OpenSearchQueryAssistToolset(),
        CoralogixToolset(),
        RabbitMQToolset(),
        BashExecutorToolset(),
        KubectlRunToolset(),
        ConfluenceToolset(),
        MongoDBAtlasToolset(),
        RunbookToolset(dal=dal, additional_search_paths=additional_search_paths),
        AzureSQLToolset(),
        ServiceNowTablesToolset(),
        DatabaseToolset(),
        ElasticsearchDataToolset(),
        ElasticsearchClusterToolset(),
    ]

    if not DISABLE_PROMETHEUS_TOOLSET:
        from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset

        toolsets.append(PrometheusToolset())

    if not USE_LEGACY_KUBERNETES_LOGS:
        toolsets.append(KubernetesLogsToolset())

    return toolsets


def _load_toolsets_from_file(
    toolsets_path: str, strict_check: bool = True
) -> List[Toolset]:
    toolsets = []
    with open(toolsets_path) as file:
        parsed_yaml = yaml.safe_load(file)
        if parsed_yaml is None:
            raise ValueError(
                f"Failed to load toolsets from {toolsets_path}: file is empty or invalid YAML."
            )
        toolsets_dict = parsed_yaml.get("toolsets", {})
        mcp_config = parsed_yaml.get("mcp_servers", {})

        for server_config in mcp_config.values():
            server_config["type"] = ToolsetType.MCP.value
            server_config.setdefault("enabled", True)

        toolsets_dict.update(mcp_config)

        toolsets.extend(_parse_toolset_config(toolsets_dict, strict_check))

    return toolsets


def _is_old_toolset_config(
    toolsets: Union[dict[str, dict[str, Any]], List[dict[str, Any]]],
) -> bool:
    # old config is a list of toolsets
    if isinstance(toolsets, list):
        return True
    return False


def _parse_toolset_config(
    toolsets: dict[str, dict[str, Any]],
    strict_check: bool = True,
) -> List[Toolset]:
    """Parse toolset config dicts into Toolset objects.

    :param toolsets: Dictionary of toolsets.
    :param strict_check: If True, all required fields for a toolset must be present.
    :return: List of validated Toolset objects.
    """
    from holmes.plugins.toolsets.database.database import DatabaseToolset
    from holmes.plugins.toolsets.http.http_toolset import HttpToolset
    from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset
    from holmes.plugins.toolsets.mongodb.mongodb import MongoDBToolset

    if not toolsets:
        return []

    loaded_toolsets: list[Toolset] = []
    if _is_old_toolset_config(toolsets):
        message = "Old toolset config format detected, please update to the new format: https://holmesgpt.dev/data-sources/custom-toolsets/"
        logging.warning(message)
        raise ValueError(message)

    for name, config in toolsets.items():
        try:
            toolset_type = config.get("type", ToolsetType.BUILTIN.value)

            # Resolve env var placeholders before creating the Toolset.
            # If done after, .override_with() will overwrite resolved values with placeholders
            # because model_dump() returns the original, unprocessed config from YAML.
            #
            # For MCP servers, preserve extra_headers templates so they can be
            # dynamically resolved at request time (e.g., for refreshing tokens).
            saved_extra_headers = None
            if toolset_type == ToolsetType.MCP.value and isinstance(
                config.get("config"), dict
            ):
                saved_extra_headers = config["config"].pop("extra_headers", None)

            if config:
                config = env_utils.replace_env_vars_values(config)

            if saved_extra_headers is not None:
                config.setdefault("config", {})["extra_headers"] = saved_extra_headers

            validated_toolset: Optional[Toolset] = None
            # MCP server is not a built-in toolset, so we need to set the type explicitly
            if toolset_type == ToolsetType.MCP.value:
                validated_toolset = RemoteMCPToolset(**config, name=name)
            elif toolset_type == ToolsetType.HTTP.value:
                validated_toolset = HttpToolset(name=name, **config)
            elif toolset_type == ToolsetType.DATABASE.value:
                validated_toolset = DatabaseToolset(name=name, **config)
            elif toolset_type == ToolsetType.MONGODB.value:
                validated_toolset = MongoDBToolset(name=name, **config)
            elif strict_check:
                validated_toolset = YAMLToolset(**config, name=name)  # type: ignore
            else:
                validated_toolset = ToolsetYamlFromConfig(  # type: ignore
                    **config, name=name
                )

            loaded_toolsets.append(validated_toolset)
        except ValidationError as e:
            logging.warning(f"Toolset '{name}' is invalid: {e}")

        except Exception:
            logging.warning("Failed to load toolset: %s", name, exc_info=True)

    return loaded_toolsets


def _merge_onto(
    new_toolsets: list[Toolset],
    existing_toolsets_by_name: dict[str, Toolset],
) -> None:
    """Add new or merge toolsets onto existing toolsets."""
    for new_toolset in new_toolsets:
        if new_toolset.name in existing_toolsets_by_name.keys():
            existing_toolsets_by_name[new_toolset.name].override_with(new_toolset)
        else:
            existing_toolsets_by_name[new_toolset.name] = new_toolset
