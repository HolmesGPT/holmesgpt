import logging
import os
import os.path
from typing import Any, List, Optional, Union

import yaml  # type: ignore
from pydantic import ValidationError

import holmes.utils.env as env_utils
from holmes.common.env_vars import (
    DISABLE_PROMETHEUS_TOOLSET,
    USE_LEGACY_KUBERNETES_LOGS,
)
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import Toolset, ToolsetType, ToolsetYamlFromConfig, YAMLToolset
from holmes.plugins.toolsets.atlas_mongodb.mongodb_atlas import MongoDBAtlasToolset
from holmes.plugins.toolsets.azure_sql.azure_sql_toolset import AzureSQLToolset
from holmes.plugins.toolsets.bash.bash_toolset import BashExecutorToolset
from holmes.plugins.toolsets.connectivity_check import ConnectivityCheckToolset
from holmes.plugins.toolsets.coralogix.toolset_coralogix import CoralogixToolset
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
from holmes.plugins.toolsets.git import GitToolset
from holmes.plugins.toolsets.grafana.loki.toolset_grafana_loki import GrafanaLokiToolset
from holmes.plugins.toolsets.grafana.toolset_grafana import GrafanaToolset
from holmes.plugins.toolsets.grafana.toolset_grafana_tempo import GrafanaTempoToolset
from holmes.plugins.toolsets.internet.internet import InternetToolset
from holmes.plugins.toolsets.internet.notion import NotionToolset
from holmes.plugins.toolsets.investigator.core_investigation import (
    CoreInvestigationToolset,
)
from holmes.plugins.toolsets.kafka import KafkaToolset
from holmes.plugins.toolsets.kubernetes_logs import KubernetesLogsToolset
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset
from holmes.plugins.toolsets.newrelic.newrelic import NewRelicToolset
from holmes.plugins.toolsets.elasticsearch.opensearch_query_assist import (
    OpenSearchQueryAssistToolset,
)
from holmes.plugins.toolsets.rabbitmq.toolset_rabbitmq import RabbitMQToolset
from holmes.plugins.toolsets.robusta.robusta import RobustaToolset
from holmes.plugins.toolsets.runbook.runbook_fetcher import RunbookToolset
from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import (
    ServiceNowTablesToolset,
)
from holmes.plugins.toolsets.elasticsearch.elasticsearch import (
    ElasticsearchClusterToolset,
    ElasticsearchDataToolset,
)

THIS_DIR = os.path.abspath(os.path.dirname(__file__))


def load_toolsets_from_file(
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

        toolsets.extend(load_toolsets_from_config(toolsets_dict, strict_check))

    return toolsets


def load_python_toolsets(
    dal: Optional[SupabaseDal],
    additional_search_paths: Optional[List[str]] = None,
) -> List[Toolset]:
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
        GitToolset(),
        BashExecutorToolset(),
        MongoDBAtlasToolset(),
        RunbookToolset(dal=dal, additional_search_paths=additional_search_paths),
        AzureSQLToolset(),
        ServiceNowTablesToolset(),
        ElasticsearchDataToolset(),
        ElasticsearchClusterToolset(),
    ]

    if not DISABLE_PROMETHEUS_TOOLSET:
        from holmes.plugins.toolsets.prometheus.prometheus import PrometheusToolset

        toolsets.append(PrometheusToolset())

    if not USE_LEGACY_KUBERNETES_LOGS:
        toolsets.append(KubernetesLogsToolset())

    return toolsets


def load_builtin_toolsets(
    dal: Optional[SupabaseDal] = None,
    additional_search_paths: Optional[List[str]] = None,
) -> List[Toolset]:
    all_toolsets: List[Toolset] = []
    logging.debug(f"loading toolsets from {THIS_DIR}")

    # Handle YAML toolsets
    for filename in os.listdir(THIS_DIR):
        if not filename.endswith(".yaml"):
            continue

        if filename == "kubernetes_logs.yaml" and not USE_LEGACY_KUBERNETES_LOGS:
            continue

        path = os.path.join(THIS_DIR, filename)
        toolsets_from_file = load_toolsets_from_file(path, strict_check=True)
        all_toolsets.extend(toolsets_from_file)

    all_toolsets.extend(
        load_python_toolsets(dal=dal, additional_search_paths=additional_search_paths)
    )  # type: ignore

    # disable built-in toolsets by default, and the user can enable them explicitly in config.
    for toolset in all_toolsets:
        toolset.type = ToolsetType.BUILTIN
        # dont' expose build-in toolsets path
        toolset.path = None

    return all_toolsets  # type: ignore


def is_old_toolset_config(
    toolsets: Union[dict[str, dict[str, Any]], List[dict[str, Any]]],
) -> bool:
    # old config is a list of toolsets
    if isinstance(toolsets, list):
        return True
    return False


def load_toolsets_from_config(
    toolsets: dict[str, dict[str, Any]],
    strict_check: bool = True,
) -> List[Toolset]:
    """
    Load toolsets from a dictionary or list of dictionaries.
    :param toolsets: Dictionary of toolsets or list of toolset configurations.
    :param strict_check: If True, all required fields for a toolset must be present.
    :return: List of validated Toolset objects.
    """

    if not toolsets:
        return []

    loaded_toolsets: list[Toolset] = []
    if is_old_toolset_config(toolsets):
        message = "Old toolset config format detected, please update to the new format: https://holmesgpt.dev/data-sources/custom-toolsets/"
        logging.warning(message)
        raise ValueError(message)

    for name, config in toolsets.items():
        try:
            toolset_type = config.get("type", ToolsetType.BUILTIN.value)

            # Resolve env var placeholders before creating the Toolset.
            # If done after, .override_with() will overwrite resolved values with placeholders
            # because model_dump() returns the original, unprocessed config from YAML.
            if config:
                config = env_utils.replace_env_vars_values(config)

            validated_toolset: Optional[Toolset] = None
            # MCP server is not a built-in toolset, so we need to set the type explicitly
            if toolset_type == ToolsetType.MCP.value:
                validated_toolset = RemoteMCPToolset(**config, name=name)
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
