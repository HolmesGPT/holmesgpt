"""
MkDocs hook: the checks of the deployment fences that need Holmes.

docs/custom_fences.py renders the fences and runs the checks that read only files; it imports
nothing from `holmes`. This hook checks each fence's `toolsets` and `mcp_servers` blocks against
Holmes's toolsets, on every page, and fails the build with an error naming the page and the
fence's line. A tool that expands the fences without Holmes installed loads mkdocs.yml without its
hooks (`hooks=[]`).

Blocks. A block holds only the keys pages write for its kind, each with a value of its type: a
built-in toolset (the name of one), a toolset of a type (`type:` names one of the types pages
use), a YAML toolset (any other `toolsets` name; it defines its own `tools`, each a `name`, a
`description` and a `command`, and its `prerequisites`, each an `env` list or a `command`), or an
MCP server (an `mcp_servers` entry).

Toolset configs. A block's `config` must be one that a config class of its toolset accepts: the
built-in toolset of that name, else the toolset of the type `type:` names, and the MCP toolset for
an `mcp_servers` entry; with `subtype:`, the class of that subtype. It is checked with each
`<placeholder>` replaced and with only the group's environment set: its secret's keys and its
`additionalEnvVars`. Every key of the config, and of each nested config the class declares as a
model (`Model`, `Optional[Model]` or `List[Model]`), is a field the class declares: config classes
accept undeclared keys (`extra="allow"`) to keep deprecated names working, and a page shows only
current names.

Helm values. `check_fence` checks a fence's Helm values against what reads them: the chart
renders them with `helm template`, every value changes the render when it changes (else no
template reads it), and every rendered object matches its Kubernetes API schema. tests/docs runs
it on every fence; it needs Helm on PATH and kubernetes-validate.
"""

import copy
import functools
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, ClassVar, Dict, Iterator, List, Literal, Optional, Tuple, Union, get_args, get_origin

import yaml  # type: ignore
from pydantic import BaseModel, Field, ValidationError

from docs import custom_fences as cf
from holmes.config import Config
from holmes.core.tools import ToolsetType
from holmes.plugins.toolsets import load_builtin_toolsets, load_toolsets_from_config
from holmes.plugins.toolsets.multi_instance import MultiInstanceToolset

try:
    import kubernetes_validate.utils
except ImportError as e:
    raise ImportError(
        "the fence checks need kubernetes-validate, a dev dependency: run `poetry install --with dev`"
    ) from e

# A placeholder the reader replaces, such as `<namespace>`.
PLACEHOLDER_RE = re.compile(r"<[A-Za-z0-9_-]+>")


class BuiltinToolsetBlock(cf.Form):
    kind: ClassVar[str] = "a built-in toolset"
    enabled: Optional[bool] = None
    subtype: Optional[cf.Text] = None
    config: Optional[cf.Mapping] = None


class TypedToolsetBlock(cf.Form):
    kind: ClassVar[str] = "a toolset with a `type:`"
    type: Literal["database", "mongodb", "http"]
    enabled: Optional[bool] = None
    description: Optional[cf.Text] = None
    llm_instructions: Optional[cf.Text] = None
    config: Optional[cf.Mapping] = None


class YamlTool(cf.Form):
    name: cf.Text
    description: cf.Text
    command: cf.Text


class EnvPrerequisite(cf.Form):
    env: Annotated[List[cf.Text], Field(min_length=1)]


class CommandPrerequisite(cf.Form):
    command: cf.Text


class YamlToolsetBlock(cf.Form):
    kind: ClassVar[str] = "a YAML toolset (a name that is no built-in toolset, with no `type:`)"
    description: Optional[cf.Text] = None
    installation_instructions: Optional[cf.Text] = None
    prerequisites: Optional[Annotated[List[Union[EnvPrerequisite, CommandPrerequisite]], Field(min_length=1)]] = None
    tools: Annotated[List[YamlTool], Field(min_length=1)]


class McpServerBlock(cf.Form):
    kind: ClassVar[str] = "an MCP server"
    description: Optional[cf.Text] = None
    llm_instructions: Optional[cf.Text] = None
    icon_url: Optional[cf.Text] = None
    config: Optional[cf.Mapping] = None


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


def _is_model(annotation) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _holds_model(annotation) -> bool:
    return _is_model(annotation) or any(_holds_model(arg) for arg in get_args(annotation))


def _nested_model(cls: type, key: str) -> Tuple[Optional[type], bool]:
    """(the model a field holds, whether it holds a list of them), through the forms the
    config classes use: `Model`, `Optional[Model]` and `List[Model]`; (None, False) for
    a field that holds no model. A model held in any other form fails the build, so the
    check cannot pass over the keys under it."""
    annotation = cls.model_fields[key].annotation
    args = get_args(annotation)
    if _is_model(annotation):
        return annotation, False
    if get_origin(annotation) is Union and len(args) == 2 and type(None) in args:
        (inner,) = [arg for arg in args if arg is not type(None)]
        if _is_model(inner):
            return inner, False
    if get_origin(annotation) is list and len(args) == 1 and _is_model(args[0]):
        return args[0], True
    if _holds_model(annotation):
        raise cf.FenceBodyError(
            f"{cls.__name__}.{key} holds a model as {annotation}, which the config check does not "
            "read: it reads Model, Optional[Model] and List[Model] (docs/fence_checks.py)"
        )
    return None, False


def _undeclared_key(config: dict, cls: type, path: str = "") -> Optional[str]:
    """The first key of `config`, nested configs and their list entries included, that
    `cls` does not declare."""
    for key, value in config.items():
        if key not in cls.model_fields:
            return path + key
        nested, many = _nested_model(cls, key)
        if nested is None:
            continue
        entries = enumerate(value) if many else [(None, value)]
        for index, entry in entries:
            # An `Optional[Model]` field written with no value holds no keys.
            if entry is None:
                continue
            where = path + key + ("" if index is None else f"[{index}]") + "."
            undeclared = _undeclared_key(entry, nested, where)
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


def check_page(markdown: str, page: str, offset: int = 0) -> None:
    """Fail the build on a deployment fence of the page whose blocks Holmes refuses;
    the page's markdown starts `offset` lines into its source."""
    for fence in cf.deployment_fences(markdown, page, offset):
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
    check_page(markdown, page.file.src_uri, cf.source_line_offset(markdown, page))
    return markdown


# The Kubernetes version whose API schemas, as kubernetes-validate bundles them, the rendered
# objects are checked against.
KUBERNETES_VERSION = "1.36"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        raise RuntimeError("the fence checks need Helm on PATH: install Helm 3 or later, https://helm.sh/docs/intro/install/")
    return helm


def _render(values: dict, chart_dir: Path) -> subprocess.CompletedProcess:
    """`helm template` of the chart with `values`, under the release name the docs' commands use."""
    return subprocess.run(
        [_helm(), "template", "holmes", str(chart_dir), "-f", "-"],
        input=yaml.safe_dump(values),
        capture_output=True,
        text=True,
    )


def _without_checksums(render: str) -> str:
    # A checksum annotation hashes a whole values subtree, so it changes with a value no template reads.
    return "\n".join(line for line in render.split("\n") if "checksum/" not in line)


def _leaves(node, path: tuple = ()) -> Iterator[Tuple[tuple, object]]:
    """(key path, value) of every leaf under the mapping or list `node`: each scalar at any
    depth, list items included, and each empty mapping, empty list and null."""
    for key, value in node.items() if isinstance(node, dict) else enumerate(node):
        if isinstance(value, (dict, list)) and value:
            yield from _leaves(value, path + (key,))
        else:
            yield path + (key,), value


def _changed(value):
    """A value other than `value`, of its type where YAML gives the chart one: an empty
    mapping or list gains an entry, and null becomes a string."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if value is None:
        return "changed"
    if value == {}:
        return {"changed": "changed"}
    if value == []:
        return ["changed"]
    return f"{value}-changed"


def _with(values: dict, path: tuple, value) -> dict:
    changed = copy.deepcopy(values)
    node = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return changed


def _key_path(path: tuple) -> str:
    return "".join(f"[{key}]" if isinstance(key, int) else f".{key}" for key in path).lstrip(".")


def _unread(values: dict, render: str, chart_dir: Path) -> List[str]:
    """The key path of each leaf of `values` whose change leaves the chart's `render` as it
    is, so no template reads it. A change Helm refuses counts as read."""
    base = _without_checksums(render)

    def unchanged(leaf) -> bool:
        path, value = leaf
        result = _render(_with(values, path, _changed(value)), chart_dir)
        return result.returncode == 0 and _without_checksums(result.stdout) == base

    leaves = list(_leaves(values))
    with ThreadPoolExecutor() as pool:
        return [_key_path(path) for (path, _), same in zip(leaves, pool.map(unchanged, leaves)) if same]


def _schema_errors(render: str) -> List[str]:
    """Why the Kubernetes API schemas refuse each object of the render, in strict mode, which
    refuses a field the schema does not declare."""
    errors = []
    for document in yaml.safe_load_all(render):
        if not document:
            continue
        name = f"{document['kind']} {document['metadata']['name']}"
        try:
            kubernetes_validate.utils.validate(document, KUBERNETES_VERSION, strict=True)
        except kubernetes_validate.utils.ValidationError as e:
            where = ".".join(map(str, e.path))
            errors.append(f"the rendered {name} does not match its Kubernetes {KUBERNETES_VERSION} schema at `{where}`: {e.message}")
        except kubernetes_validate.utils.SchemaNotFoundError:
            errors.append(
                f"the rendered {name} has no Kubernetes {KUBERNETES_VERSION} schema in kubernetes-validate: "
                f"kind {document['kind']}, apiVersion {document['apiVersion']}"
            )
    return errors


def check_fence(values: dict, environment: Dict[str, str], chart_dir: Path) -> List[str]:
    """What the chart in `chart_dir` and the Kubernetes API refuse in a fence's Helm values,
    as the Holmes Helm Chart tab shows them; `environment` is the variables the group gives
    Holmes, its secret's keys and its `additionalEnvVars`.

    The values must render with `helm template`, every leaf must change the render when it
    changes (else no template reads it), and every rendered object must match its
    Kubernetes schema."""
    result = _render(values, chart_dir)
    if result.returncode != 0:
        return [f"helm template fails: {result.stderr.strip()}"]
    unread = [f"`{path}`: the chart renders the same when it changes, so no template reads it" for path in _unread(values, result.stdout, chart_dir)]
    return unread + _schema_errors(result.stdout)
