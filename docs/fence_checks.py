"""
MkDocs hook: the checks of the deployment fences that need Holmes.

docs/custom_fences.py renders the fences and runs the checks that read only files; it imports
nothing from `holmes`. This hook checks each fence's `toolsets` and `mcp_servers` blocks against
Holmes's toolsets, on every page, and fails the build with an error naming the page and the
fence's line. A tool that expands the fences without Holmes installed loads mkdocs.yml without its
hooks (`hooks=[]`).

Blocks. A block holds only the keys pages write for its kind, each with a value of its type: a
built-in toolset (the name of one), a toolset of a type (`type:` names one of the types pages
use), a YAML toolset (any other `toolsets` name; it defines its own `tools`), or an MCP server (an
`mcp_servers` entry).

Toolset configs. A block's `config` must be one that a config class of its toolset accepts: the
built-in toolset of that name, else the toolset of the type `type:` names, and the MCP toolset for
an `mcp_servers` entry; with `subtype:`, the class of that subtype. It is checked with each
`<placeholder>` replaced and with only the group's environment set: its secret's keys and its
`additionalEnvVars`. Every key of the config, and of a nested config the class declares as a
model, is a field the class declares: config classes accept undeclared keys (`extra="allow"`) to
keep deprecated names working, and a page shows only current names.
"""

import functools
import os
import re
from contextlib import contextmanager
from typing import Annotated, Any, ClassVar, Dict, List, Literal, Optional, get_args

from pydantic import BaseModel, Field, ValidationError

from docs import custom_fences as cf
from holmes.config import Config
from holmes.core.tools import ToolsetType
from holmes.plugins.toolsets import load_builtin_toolsets, load_toolsets_from_config
from holmes.plugins.toolsets.multi_instance import MultiInstanceToolset

# A placeholder the reader replaces, such as `<namespace>`.
PLACEHOLDER_RE = re.compile(r"<[A-Za-z0-9_-]+>")


Mapping = Annotated[Dict[str, Any], Field(min_length=1)]
Entries = Annotated[List[Dict[str, Any]], Field(min_length=1)]


class BuiltinToolsetBlock(cf.Form):
    kind: ClassVar[str] = "a built-in toolset"
    enabled: Optional[bool] = None
    subtype: Optional[cf.Text] = None
    config: Optional[Mapping] = None


class TypedToolsetBlock(cf.Form):
    kind: ClassVar[str] = "a toolset with a `type:`"
    type: Literal["database", "mongodb", "http"]
    enabled: Optional[bool] = None
    description: Optional[cf.Text] = None
    llm_instructions: Optional[cf.Text] = None
    config: Optional[Mapping] = None


class YamlToolsetBlock(cf.Form):
    kind: ClassVar[str] = "a YAML toolset (a name that is no built-in toolset, with no `type:`)"
    description: Optional[cf.Text] = None
    installation_instructions: Optional[cf.Text] = None
    prerequisites: Optional[Entries] = None
    tools: Entries


class McpServerBlock(cf.Form):
    kind: ClassVar[str] = "an MCP server"
    description: Optional[cf.Text] = None
    llm_instructions: Optional[cf.Text] = None
    icon_url: Optional[cf.Text] = None
    config: Optional[Mapping] = None


@functools.cache
def _builtin_toolsets() -> dict:
    return {toolset.name: toolset for toolset in load_builtin_toolsets()}


def _config_classes(part: str, name: str, block: dict) -> list:
    """The config classes of the toolset a values block configures: the built-in
    toolset of that name, else the toolset of the type `type:` names, and the MCP
    toolset for an `mcp_servers` entry, as Holmes's loader resolves them."""
    if part == "toolsets" and name in _builtin_toolsets():
        toolset = _builtin_toolsets()[name]
        # A multi-instance wrapper validates each instance with its child's classes.
        owner = toolset._child_cls if isinstance(toolset, MultiInstanceToolset) else type(toolset)
        return list(owner.config_classes)
    toolset_type = ToolsetType.MCP.value if part == "mcp_servers" else block.get("type")
    if toolset_type is None:
        return []
    (toolset,) = load_toolsets_from_config({name: {"type": toolset_type}})
    return list(type(toolset).config_classes)


def _block_form(part: str, name: str, block: dict):
    """The form of a values block's kind, as Holmes's toolset manager tells the kinds apart."""
    if part == "mcp_servers":
        return McpServerBlock
    if name in _builtin_toolsets():
        return BuiltinToolsetBlock
    return TypedToolsetBlock if "type" in block else YamlToolsetBlock


def _check_blocks(values: dict) -> None:
    """Raise if a `toolsets` or `mcp_servers` block is not in the form of its kind."""
    for part in ("toolsets", "mcp_servers"):
        for name, block in values.get(part, {}).items():
            form = _block_form(part, name, block)
            try:
                form.model_validate(block)
            except ValidationError as e:
                raise cf.FenceBodyError(f"`{part}.{name}` is not in a form pages write for {form.kind}: {e}") from e


def _filled(value):
    """`value` with each `<placeholder>` replaced by its name, as a reader replaces it."""
    if isinstance(value, dict):
        return {key: _filled(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_filled(item) for item in value]
    if isinstance(value, str):
        return PLACEHOLDER_RE.sub(lambda match: match[0][1:-1], value)
    return value


def _model(annotation) -> Optional[type]:
    """The pydantic model a field's annotation holds, `Optional` or not."""
    for candidate in (annotation, *get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _undeclared_key(config: dict, cls: type, path: tuple = ()) -> Optional[str]:
    """The first key of `config`, nested configs included, that `cls` does not declare."""
    for key, value in config.items():
        field = cls.model_fields.get(key)
        if field is None:
            return ".".join(path + (key,))
        nested = _model(field.annotation)
        if nested is not None and isinstance(value, dict):
            undeclared = _undeclared_key(value, nested, path + (key,))
            if undeclared:
                return undeclared
    return None


@contextmanager
def _environment(variables: dict):
    """Run with only these environment variables set."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(variables)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _check_toolset_configs(values: dict, environment: dict) -> None:
    """Raise if a `toolsets` or `mcp_servers` block's `config` is one its toolset's
    config classes refuse (the class `subtype:` names, when given), with Holmes
    running with `environment`, the variables the group gives it."""
    for part in ("toolsets", "mcp_servers"):
        for name, block in values.get(part, {}).items():
            classes = _config_classes(part, name, block)
            if not classes:
                continue
            subtype = block.get("subtype")
            if subtype is not None:
                classes = [cls for cls in classes if getattr(cls, "_subtype", None) == subtype]
                if not classes:
                    raise cf.FenceBodyError(f"`{part}.{name}.subtype` names no config of the toolset: {subtype!r}")
            config = _filled(block.get("config") or {})
            errors = []
            with _environment(environment):
                for cls in classes:
                    try:
                        cls.model_validate(config)
                    except (ValidationError, ValueError) as e:
                        errors.append(f"{cls.__name__}: {e}")
                        continue
                    undeclared = _undeclared_key(config, cls)
                    if undeclared is None:
                        break
                    errors.append(f"{cls.__name__}: `{undeclared}` is not a field it declares")
                else:
                    raise cf.FenceBodyError(
                        f"`{part}.{name}.config` is not a config the toolset accepts: " + "; ".join(errors)
                    )


def check_page(markdown: str, page: str) -> None:
    """Fail the build on a deployment fence of the page whose blocks Holmes refuses."""
    for fence in cf.deployment_fences(markdown, page):
        try:
            _check_blocks(fence.values)
            _check_toolset_configs(fence.values, fence.environment)
        except cf.FenceBodyError as e:
            raise cf.TabFenceError(f"{page}:{fence.line}: {e}") from e


def on_config(config, **kwargs):
    """MkDocs hook: fail the build when the CLI tab's keys are not the chart's Holmes config keys."""
    derived = frozenset(Config.model_fields) & cf.CHART_KEYS
    if cf.CLI_CONFIG_KEYS != derived:
        raise cf.TabFenceError(
            f"docs/custom_fences.py: CLI_CONFIG_KEYS is {sorted(cf.CLI_CONFIG_KEYS)}, but the "
            f"holmes.config.Config fields that are Holmes chart values are {sorted(derived)}"
        )
    return config


def on_page_markdown(markdown, page, config, **kwargs):
    """MkDocs hook: check the page's deployment fences."""
    check_page(markdown, page.file.src_uri)
    return markdown
