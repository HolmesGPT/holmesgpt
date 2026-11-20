import hashlib
import json
import re
import yaml
import logging
from datetime import datetime
from typing import Any, List

import fnmatch

from holmes.config import Config
from holmes.core.supabase_dal import SupabaseDal
from holmes.core.tools import (
    Tool,
    ToolParameter,
    Toolset,
    ToolsetDBModel,
    ToolsetStatusEnum,
)
from holmes.core.tools_utils.tool_executor import _mcp_tool_name
from holmes.plugins.prompts import load_and_render_prompt
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset
from holmes.plugins.toolsets.multi_instance import INSTANCE_PARAM, INSTANCE_PARAM_NAME
from holmes.version import get_version

REMOTE_TOOLS_SCHEMA_VERSION = "v1"


def _tool_requires_approval(tool_name: str, approval_patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(tool_name, p) for p in approval_patterns or [])


def _exposed_tools(toolset: Toolset, tool_executor: Any) -> List[Any]:
    """Toolset's tools carrying the executor's exposed (collision-safe) names."""
    if tool_executor is None:
        return list(toolset.tools)
    return [
        tool_executor.tools_by_name[name]
        for name, ts in tool_executor._tool_to_toolset.items()
        if ts is toolset and name in tool_executor.tools_by_name
    ]


# Keys that annotate a JSON schema without changing what it accepts.
_SCHEMA_ANNOTATIONS = frozenset(
    {"description", "title", "examples", "example", "default", "$comment", "$schema"}
)


def _canonical_order(values: list) -> list:
    return sorted(values, key=lambda v: json.dumps(v, sort_keys=True, default=str))


# Keywords whose value is a schema, a list of schemas or a map of names to
# schemas. Everything else is a literal (const, enum, pattern, ...) and is
# hashed as published.
_SCHEMA_VALUED = frozenset(
    {
        "items",
        "prefixItems",
        "additionalItems",
        "additionalProperties",
        "unevaluatedItems",
        "unevaluatedProperties",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
    }
)
_SCHEMA_MAPS = frozenset(
    {"properties", "patternProperties", "dependentSchemas", "$defs", "definitions"}
)
# Combinators whose branches accept the same inputs in any order.
_SCHEMA_UNORDERED = frozenset({"anyOf", "oneOf", "allOf"})


def _schema_contract(schema: Any) -> Any:
    """A raw JSON schema (an MCP tool's `additionalProperties`, or
    `json_schema_extra`) without annotations. Only schema positions are
    walked, so a literal such as a `const` object keeps every key."""
    if not isinstance(schema, dict):
        return schema
    contract: dict = {}
    for key, value in schema.items():
        if key in _SCHEMA_ANNOTATIONS:
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            contract[key] = {n: _schema_contract(v) for n, v in value.items()}
        elif key in _SCHEMA_UNORDERED and isinstance(value, list):
            contract[key] = _canonical_order([_schema_contract(v) for v in value])
        elif key in _SCHEMA_VALUED and isinstance(value, list):
            contract[key] = [_schema_contract(v) for v in value]
        elif key in _SCHEMA_VALUED:
            contract[key] = _schema_contract(value)
        elif key in ("enum", "required", "type") and isinstance(value, list):
            contract[key] = _canonical_order(value)
        else:
            contract[key] = value
    return contract


_ARRAY_OF = re.compile(r"^array\[(?P<inner>\w+)\]$")


def _parameter_contract(param: ToolParameter) -> dict:
    # exclude_defaults: a field added to ToolParameter later leaves the hash of
    # every parameter that does not set it unchanged.
    contract = param.model_dump(
        exclude={"description", "properties", "items", "any_of"},
        exclude_none=True,
        exclude_defaults=True,
    )
    # An empty enum constrains nothing: the formatter omits it and nothing
    # validates against it.
    if not contract.get("enum"):
        contract.pop("enum", None)
    if isinstance(contract.get("enum"), list):
        contract["enum"] = _canonical_order(contract["enum"])
    if isinstance(contract.get("type"), list):
        # The first non-null type is both what the formatter publishes and what
        # coerce_params converts to, so it keeps its place; the rest only say
        # what else the tool accepts.
        types = contract["type"]
        others = [t for t in types if t != "null"]
        canonical = (
            others[:1] + _canonical_order(others[1:]) + (["null"] if "null" in types else [])
        )
        contract["type"] = canonical[0] if len(canonical) == 1 else canonical
        if contract["type"] == "string":
            del contract["type"]  # the default, as exclude_defaults drops it
    raw_type = contract.get("type")
    array_of = _ARRAY_OF.match(raw_type) if isinstance(raw_type, str) else None
    if array_of and param.items is None:
        # "array[string]" publishes the same schema as an array of strings.
        contract["type"] = "array"
        contract["items"] = _parameter_contract(ToolParameter(type=array_of["inner"]))
    for key in ("additional_properties", "json_schema_extra"):
        if key in contract:
            contract[key] = _schema_contract(contract[key])
            # An empty schema (say, json_schema_extra holding only a default)
            # constrains nothing.
            if contract[key] == {}:
                del contract[key]
    if param.properties is not None:
        contract["properties"] = {
            name: _parameter_contract(p) for name, p in param.properties.items()
        }
    if param.items is not None:
        contract["items"] = _parameter_contract(param.items)
    if param.any_of is not None:
        contract["any_of"] = _canonical_order(
            [_parameter_contract(b) for b in param.any_of]
        )
    return contract


def remote_tool_contract_hash(tool: Tool) -> str:
    """Hash of what a caller sends this tool: parameter names, types and
    constraints, not descriptions, defaults or the strict-mode rendering. The
    platform routes a remote tool to every agent whose hash matches the newest
    Holmes version's, so this is the only place tool schemas are compared.
    The `instance` param a multi-instance toolset adds is left out: the
    platform routes it per cluster. It is recognised by identity, since the
    wrapper injects that very object, so a tool's own `instance` param counts
    even when it is equal."""
    input_schema = getattr(tool, "input_schema", None)
    if isinstance(input_schema, dict):
        # An MCP server validates against its full inputSchema, of which the
        # parsed parameters keep only part.
        contract: Any = {"input_schema": _schema_contract(input_schema)}
    else:
        contract = {
            name: _parameter_contract(param)
            for name, param in tool.parameters.items()
            if not (name == INSTANCE_PARAM_NAME and param is INSTANCE_PARAM)
        }
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_remote_tools_meta(toolset: Toolset, tool_executor: Any = None) -> Any:
    """Build the meta.remote_tools payload for a remotely-exposed toolset, or
    None when the toolset must not be published (not exposed, is_core, not
    enabled, or no publishable tools).

    Excluded tools: tools that as a whole match approval_required_tools
    patterns (bash is NOT excluded — its approval decision is per command
    and enforced at execution time)."""
    if not toolset.expose_remotely or toolset.is_core:
        return None
    if toolset.status != ToolsetStatusEnum.ENABLED:
        return None

    exposed_instances = None
    get_instances = getattr(toolset, "remote_exposed_instances", None)
    if callable(get_instances):
        exposed_instances = get_instances()
        if exposed_instances is not None and not exposed_instances:
            return None  # multi-instance toolset with zero exposed instances

    tools = [
        {**tool.get_openai_format(), "contract_hash": remote_tool_contract_hash(tool)}
        for tool in _exposed_tools(toolset, tool_executor)
        if not _tool_requires_approval(
            _mcp_tool_name(tool) or tool.name, toolset.approval_required_tools
        )
    ]
    if not tools:
        return None

    return {
        "schema_version": REMOTE_TOOLS_SCHEMA_VERSION,
        "holmes_version": get_version(),
        "exposed_instances": exposed_instances,
        "tools": tools,
    }


def log_toolsets_statuses(toolsets: List[Toolset]):
    enabled_toolsets = [
        toolset.name for toolset in toolsets if toolset.status.value == "enabled"
    ]
    disabled_toolsets = [
        toolset.name for toolset in toolsets if toolset.status.value != "enabled"
    ]
    logging.info(f"Enabled toolsets: {enabled_toolsets}")
    logging.info(f"Disabled toolsets: {disabled_toolsets}")


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
    tool_executor = config.create_server_tool_executor(dal)

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

        if not toolset.installation_instructions:
            instructions = get_config_schema_for_toolset(toolset)
            toolset.installation_instructions = instructions
        # Use toolset's own meta if set (e.g., database with subtype, or a
        # multi-instance toolset's per-instance health), and always carry the
        # toolset type alongside it so setting other meta keys doesn't drop it.
        meta = dict(toolset.meta) if toolset.meta else {}
        if toolset.type:
            meta.setdefault("type", toolset.type.value)
        meta = meta or None
        if isinstance(toolset, RemoteMCPToolset):
            oauth_config = toolset.get_oauth_config()
            if oauth_config:
                meta = meta or {}
                meta["oauth_config"] = oauth_config

        # Publish llm_instructions at the top level of meta for EVERY toolset
        # (not just remotely-exposed ones) — other projects consume it from
        # here. platform-mcp also reads it from this top-level key.
        if toolset.llm_instructions:
            meta = meta or {}
            meta["llm_instructions"] = toolset.llm_instructions

        remote_tools = build_remote_tools_meta(toolset, tool_executor)
        if remote_tools:
            meta = meta or {}
            meta["remote_tools"] = remote_tools

        db_toolsets.append(
            ToolsetDBModel(
                toolset_name=toolset.name,
                cluster_id=config.cluster_name,
                account_id=dal.account_id,
                updated_at=updated_at,
                icon_url=toolset.icon_url,
                status=toolset.status.value if toolset.status else None,
                error=toolset.error,
                description=toolset.description,
                docs_url=toolset.docs_url,
                installation_instructions=toolset.installation_instructions,
                meta=meta,
                version=get_version(),
            ).model_dump()
        )
    dal.sync_toolsets(db_toolsets, config.cluster_name)
    log_toolsets_statuses(tool_executor.toolsets)


def get_config_schema_for_toolset(toolset: Toolset) -> str:
    res: dict = {
        "example_yaml": render_default_installation_instructions_for_toolset(toolset),
        "schema": toolset.get_config_schema(),
    }
    return json.dumps(res)

def render_default_installation_instructions_for_toolset(toolset: Toolset) -> str:
    env_vars = toolset.get_environment_variables()
    context: dict[str, Any] = {
        "env_vars": env_vars if env_vars else [],
        "toolset_name": toolset.name,
    }

    example_config = toolset.get_config_example()
    if example_config:
        context["example_config"] = yaml.dump(example_config)

    # Emit top-level `subtype:` in the example YAML for multi-variant toolsets
    # (e.g. Prometheus, Database) so users who copy the example verbatim land
    # on the correct variant. The ToolsetConfig subclass declares `_subtype`.
    if toolset.config_classes:
        subtype = getattr(toolset.config_classes[0], "_subtype", None)
        if subtype:
            context["subtype"] = subtype

    installation_instructions = load_and_render_prompt(
        "file://holmes/utils/default_toolset_installation_guide.jinja2", context
    )
    return installation_instructions
