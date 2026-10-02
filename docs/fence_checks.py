"""
MkDocs hook: the checks of the deployment fences that need Holmes.

docs/custom_fences.py renders the fences and runs the checks that read only files; it imports
nothing from `holmes`. This hook checks each fence's `toolsets` and `mcp_servers` blocks against
Holmes's toolsets, on every page, and fails the build with an error naming the page and the
fence's line. A tool that expands the fences without Holmes installed loads mkdocs.yml without its
hooks (`hooks=[]`).

Toolset configs. A block's `config` must be one that a config class of its toolset accepts: the
built-in toolset of that name, else the toolset of the type `type:` names, and the MCP toolset for
an `mcp_servers` entry; with `subtype:`, the class of that subtype. It is checked with each
`<placeholder>` replaced and with only the group's environment set: its secret's keys and its
`additionalEnvVars`.
"""

import functools
import os
import re
from contextlib import contextmanager

from pydantic import ValidationError

from docs import custom_fences as cf
from holmes.config import Config
from holmes.core.tools import ToolsetType
from holmes.plugins.toolsets import load_builtin_toolsets, load_toolsets_from_config
from holmes.plugins.toolsets.multi_instance import MultiInstanceToolset

# A placeholder the reader replaces, such as `<namespace>`.
PLACEHOLDER_RE = re.compile(r"<[A-Za-z0-9_-]+>")


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
    try:
        ToolsetType(toolset_type)
    except ValueError as e:
        raise cf.FenceBodyError(f"`{part}.{name}.type` is not a toolset type: {toolset_type!r}") from e
    (toolset,) = load_toolsets_from_config({name: {"type": toolset_type}})
    return list(type(toolset).config_classes)


def _filled(value):
    """`value` with each `<placeholder>` replaced by its name, as a reader replaces it."""
    if isinstance(value, dict):
        return {key: _filled(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_filled(item) for item in value]
    if isinstance(value, str):
        return PLACEHOLDER_RE.sub(lambda match: match[0][1:-1], value)
    return value


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
                        break
                    except (ValidationError, ValueError) as e:
                        errors.append(f"{cls.__name__}: {e}")
                else:
                    raise cf.FenceBodyError(
                        f"`{part}.{name}.config` is not a config the toolset accepts: " + "; ".join(errors)
                    )


def check_page(markdown: str, page: str) -> None:
    """Fail the build on a deployment fence of the page whose blocks Holmes refuses."""
    for fence in cf.deployment_fences(markdown, page):
        try:
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
