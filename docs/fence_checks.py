"""
The checks of a deployment fence's Helm values against what reads them: the Holmes chart, the
Kubernetes API for what its templates pass through into an object, and Holmes for what the chart
passes on to it. tests/docs runs `check_fence` on the values of every Holmes Helm Chart tab; the
MkDocs build does not run it, and needs neither Helm, Holmes nor this module.

The chart passes `toolsets`, `mcp_servers` (with the servers of the enabled `mcpAddons`) and
`modelList` on to Holmes in the `custom-toolsets-configmap` ConfigMap, so their contents change
the render; Holmes's own loaders check them, from the rendered ConfigMap, with each `<placeholder>`
replaced as a reader replaces it.
"""

import copy
import functools
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

import yaml  # type: ignore
from pydantic import BaseModel, ValidationError

from docs.custom_fences import key_path
from holmes.core.llm import ModelEntry
from holmes.core.tools import Toolset, ToolsetStatusEnum
from holmes.core.toolset_manager import ToolsetManager
from holmes.plugins.toolsets import load_builtin_toolsets
from holmes.plugins.toolsets.multi_instance import MultiInstanceToolset, _parse_instances
from holmes.utils.env import replace_env_vars_values

try:
    import kubernetes_validate.utils
except ImportError as e:
    raise ImportError(
        "the fence checks need kubernetes-validate, a dev dependency: run `poetry install --with dev`"
    ) from e

# A placeholder the reader replaces, such as `<namespace>`.
PLACEHOLDER_RE = re.compile(r"<[A-Za-z0-9_-]+>")

# The Kubernetes version whose API schemas, as kubernetes-validate bundles them, the rendered
# objects are checked against.
KUBERNETES_VERSION = "1.36"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        raise RuntimeError(
            "the fence checks need Helm on PATH: install Helm 3 or later, https://helm.sh/docs/intro/install/"
        )
    return helm


def _render(values: dict, chart_dir: Path) -> subprocess.CompletedProcess:
    """`helm template` of the chart with `values`, under the release name the docs' commands use."""
    return subprocess.run(
        [_helm(), "template", "holmes", str(chart_dir), "-f", "-"],
        input=yaml.safe_dump(values),
        capture_output=True,
        text=True,
    )


def _lines(render: str) -> List[str]:
    # A checksum annotation hashes a whole values subtree, so it changes with a value no template reads.
    return [line for line in render.split("\n") if "checksum/" not in line]


def _differing(lines: List[str], other: List[str]) -> Set[int]:
    return {index for index, (line, other_line) in enumerate(zip(lines, other)) if line != other_line}


def _leaves(node, path: tuple = ()) -> Iterator[Tuple[tuple, object]]:
    """(key path, value) of every scalar under the mapping or list `node`, at any depth, list
    items included. The page-text check refuses an empty mapping, list or null in the values."""
    for key, value in node.items() if isinstance(node, dict) else enumerate(node):
        if isinstance(value, (dict, list)):
            yield from _leaves(value, path + (key,))
        else:
            yield path + (key,), value


def _changed(value):
    """A value other than `value`: a bool flipped, a number plus one, and any other value a string."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    return f"{value}-changed"


def _with(values: dict, path: tuple, value) -> dict:
    changed = copy.deepcopy(values)
    node = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return changed


def _unread(values: dict, render: str, chart_dir: Path) -> List[str]:
    """An error for each leaf of `values` whose change leaves the chart's `render` the same.
    A change Helm refuses, or one that changes the number of lines, counts as read."""
    leaves = list(_leaves(values))
    if not leaves:
        return []
    base = _lines(render)

    def differing(leaf) -> Optional[Set[int]]:
        path, value = leaf
        result = _render(_with(values, path, _changed(value)), chart_dir)
        lines = _lines(result.stdout)
        if result.returncode != 0 or len(lines) != len(base):
            return None
        return _differing(base, lines)

    with ThreadPoolExecutor() as pool:
        changes = list(pool.map(differing, leaves))
    # Rendered after every change, so it differs from `base` at each line holding a random value,
    # or a time that moved on while the changes rendered: a change there says nothing about a value.
    again = _lines(_render(values, chart_dir).stdout)
    if len(again) != len(base):
        return ["two renders of these values differ in their number of lines, so which values the chart reads cannot be told"]
    unstable = _differing(base, again)
    return [
        f"`{key_path(path)}`: the render does not depend on its value"
        for (path, _), lines in zip(leaves, changes)
        if lines is not None and lines <= unstable
    ]


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
            errors.append(
                f"the rendered {name} does not match its Kubernetes {KUBERNETES_VERSION} schema at `{where}`: {e.message}"
            )
        except kubernetes_validate.utils.SchemaNotFoundError:
            errors.append(
                f"the rendered {name} has no Kubernetes {KUBERNETES_VERSION} schema in kubernetes-validate: "
                f"kind {document['kind']}, apiVersion {document['apiVersion']}"
            )
    return errors


@contextmanager
def _environment(variables: Dict[str, str]):
    """Run with only these environment variables set."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(variables)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@functools.cache
def _builtin_toolsets() -> Dict[str, Toolset]:
    with _environment({}):
        return {toolset.name: toolset for toolset in load_builtin_toolsets()}


def _undeclared(data, instance, path: tuple = ()) -> Iterator[Tuple[tuple, str]]:
    """(key path, model name) of each key in `data`, a mapping as written, that the model
    validated from it has no field or alias for, at any depth. Whatever the model does with
    such a key (refuse, ignore or keep it), nothing reads it under that name."""
    if isinstance(data, dict) and isinstance(instance, BaseModel):
        fields = type(instance).model_fields
        names = {name: name for name in fields}
        names.update({info.alias: name for name, info in fields.items() if info.alias})
        for key, value in data.items():
            if key in names:
                yield from _undeclared(value, getattr(instance, names[key]), path + (key,))
            else:
                yield path + (key,), type(instance).__name__
    elif isinstance(data, dict) and isinstance(instance, dict):
        for key, value in data.items():
            if key in instance:
                yield from _undeclared(value, instance[key], path + (key,))
    elif isinstance(data, list) and isinstance(instance, list):
        for index, (item, validated) in enumerate(zip(data, instance)):
            yield from _undeclared(item, validated, path + (index,))


def _not_fields(undeclared) -> str:
    return ", ".join(f"`{key_path(path)}` is not a field of {model}" for path, model in undeclared)


def _config_errors(toolset: Toolset, where: str) -> List[str]:
    """Why no config class of the toolset takes its `config`, as Holmes substituted it; with
    `subtype:`, a class of that subtype when any class has one. A multi-instance toolset's
    config is each of its instances', as Holmes splits it, checked with the classes of the
    toolset it wraps."""
    if not isinstance(toolset.config, dict):
        return []
    owner = _builtin_toolsets().get(toolset.name, toolset)
    if isinstance(owner, MultiInstanceToolset):
        classes = owner._child_cls.config_classes
        try:
            instances = _parse_instances(toolset.config)
        except ValueError as e:
            return [f"custom_toolset.yaml `{where}.config`: Holmes refuses its instances: {e}"]
    else:
        classes = type(owner).config_classes
        instances = [("default", toolset.config)]
    if not classes:
        return []
    if toolset.subtype and any(cls._subtype for cls in classes):
        classes = [cls for cls in classes if cls._subtype == toolset.subtype]
        if not classes:
            return [f"custom_toolset.yaml `{where}.subtype`: no config class of the toolset has subtype {toolset.subtype!r}"]
    errors = []
    for name, config in instances:
        refusals = []
        for cls in classes:
            try:
                validated = cls.model_validate(copy.deepcopy(config))
            except ValidationError as e:
                refusals.append(f"{cls.__name__}: {e}")
                continue
            undeclared = list(_undeclared(config, validated))
            if not undeclared:
                break
            refusals.append(_not_fields(undeclared))
        else:
            instance = f" (instance `{name}`)" if "instances" in toolset.config else ""
            errors.append(
                f"custom_toolset.yaml `{where}.config`{instance}: no config class of the toolset takes it: " + "; ".join(refusals)
            )
    return errors


def _toolset_errors(path: Path, written: dict) -> List[str]:
    """What Holmes refuses in `written`, the `custom_toolset.yaml` at `path`, loaded as Holmes
    loads the file the chart mounts: a block it loads as a FAILED placeholder or not at all, a
    key of a block or of its config that the model it validates has no field for."""
    try:
        loaded = ToolsetManager()._load_toolsets_from_paths([str(path)], list(_builtin_toolsets()))
    except Exception as e:  # Holmes fails to load the file at all, whatever it raises.
        return [f"Holmes fails to load custom_toolset.yaml: {type(e).__name__}: {e}"]
    toolsets = {toolset.name: toolset for toolset in loaded}
    errors = []
    for part in ("toolsets", "mcp_servers"):
        for name, block in (written.get(part) or {}).items():
            where = f"{part}.{name}"
            toolset = toolsets.get(name)
            if toolset is None:
                errors.append(f"custom_toolset.yaml `{where}`: Holmes loads no toolset of this name")
            elif toolset.status == ToolsetStatusEnum.FAILED:
                errors.append(f"custom_toolset.yaml `{where}`: Holmes refuses it: {toolset.error}")
            else:
                undeclared = list(_undeclared(block, toolset))
                if undeclared:
                    errors.append(f"custom_toolset.yaml `{where}`: {_not_fields(undeclared)}")
                errors += _config_errors(toolset, where)
    return errors


def _filled(value):
    """`value` with each `<placeholder>` in a string replaced by its name, as a reader replaces it."""
    if isinstance(value, dict):
        return {key: _filled(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_filled(item) for item in value]
    if isinstance(value, str):
        return PLACEHOLDER_RE.sub(lambda match: match[0][1:-1], value)
    return value


def _model_errors(model_list: dict) -> List[str]:
    """Each entry of the rendered `model_list.yaml` that Holmes refuses, loaded as Holmes
    loads the file: its env references substituted, then validated as a `ModelEntry`."""
    errors = []
    for name, entry in model_list.items():
        try:
            ModelEntry.model_validate(replace_env_vars_values(entry))
        except ValueError as e:
            errors.append(f"model_list.yaml `{name}`: Holmes refuses it: {e}")
    return errors


def _holmes_errors(render: str, environment: Dict[str, str]) -> List[str]:
    """What Holmes refuses in the `custom-toolsets-configmap` ConfigMap of the render, with only
    `environment` set while Holmes's code runs."""
    data = next(
        (
            document["data"]
            for document in yaml.safe_load_all(render)
            if document and document["kind"] == "ConfigMap" and document["metadata"]["name"] == "custom-toolsets-configmap"
        ),
        None,
    )
    if data is None:
        return []
    custom_toolset = _filled(yaml.safe_load(data["custom_toolset.yaml"]) or {})
    model_list = _filled(yaml.safe_load(data["model_list.yaml"]) or {})
    _builtin_toolsets()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "custom_toolset.yaml"
        path.write_text(yaml.safe_dump(custom_toolset))
        with _environment(environment):
            return _toolset_errors(path, custom_toolset) + _model_errors(model_list)


def check_fence(values: dict, environment: Dict[str, str], chart_dir: Path) -> List[str]:
    """What the chart in `chart_dir`, the Kubernetes API and Holmes refuse in a fence's Helm
    values, as the Holmes Helm Chart tab shows them; `environment` is the variables the group
    gives Holmes, its secret's keys and its `additionalEnvVars`.

    The values must render with `helm template`, every leaf must change the render when it
    changes (else the render does not depend on its value), every rendered object must
    match its Kubernetes schema, and Holmes must load what the render passes on to it. A line
    that differs between two renders of `values`, such as a random token or the time, is not
    counted as a change."""
    result = _render(values, chart_dir)
    if result.returncode != 0:
        return [f"helm template fails: {result.stderr.strip()}"]
    return (
        _unread(values, result.stdout, chart_dir)
        + _schema_errors(result.stdout)
        + _holmes_errors(result.stdout, environment)
    )
