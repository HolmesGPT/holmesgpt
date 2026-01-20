import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import Toolset, ToolsetDBModel


def log_toolsets_statuses(toolsets: List[Toolset]):
    enabled_toolsets = [
        toolset.name for toolset in toolsets if toolset.status.value == "enabled"
    ]
    disabled_toolsets = [
        toolset.name for toolset in toolsets if toolset.status.value != "enabled"
    ]
    logging.info(f"Enabled toolsets: {enabled_toolsets}")
    logging.info(f"Disabled toolsets: {disabled_toolsets}")


def _json_serializer(obj: Any) -> Any:
    """Custom JSON serializer for objects not serializable by default json code."""
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def holmes_sync_toolsets_status(dal: SupabaseDal, config: Config) -> None:
    """
    Method for synchronizing toolsets with the database:
    1) Fetch all built-in toolsets from the holmes/plugins/toolsets directory
    2) Load custom toolsets defined in /etc/holmes/config/custom_toolset.yaml
    3) Override default toolsets with corresponding custom configurations
       and add any new custom toolsets that are not part of the defaults
    4) Run the check_prerequisites method for each toolset
    5) Use sync_toolsets to upsert toolset's status and remove toolsets that are not loaded from configs or folder with default directory
    """
    tool_executor = config.create_tool_executor(dal)

    if not config.cluster_name:
        raise Exception(
            "Cluster name is missing in the configuration. Please ensure 'CLUSTER_NAME' is defined in the environment variables, "
            "or verify that a cluster name is provided in the Robusta configuration file."
        )

    db_toolsets = []
    updated_at = datetime.now().isoformat()
    for toolset in tool_executor.toolsets:
        # hiding disabled experimental toolsets from the docs
        if toolset.experimental and not toolset.enabled:
            continue

        # Get config schema for frontend form generation
        config_schema = toolset.get_config_schema()
        config_schema_json = (
            json.dumps(config_schema, default=_json_serializer)
            if config_schema
            else None
        )

        db_toolsets.append(
            ToolsetDBModel(
                **toolset.model_dump(exclude_none=True),
                toolset_name=toolset.name,
                cluster_id=config.cluster_name,
                account_id=dal.account_id,
                updated_at=updated_at,
                installation_instructions=config_schema_json,
            ).model_dump()
        )
    dal.sync_toolsets(db_toolsets, config.cluster_name)
    log_toolsets_statuses(tool_executor.toolsets)
