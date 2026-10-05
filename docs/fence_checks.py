"""
The checks of a deployment fence's Helm values against what reads them: the Holmes chart, and the
Kubernetes API for what its templates pass through into an object. tests/docs runs `check_fence`
on the values of every Holmes Helm Chart tab; the MkDocs build does not run it, and needs neither
Helm nor this module.

The chart passes `toolsets`, `mcp_servers` and `modelList` on to Holmes in a ConfigMap, so their
contents change the render and pass; what Holmes does with them is not checked here.
"""

import copy
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import yaml  # type: ignore

from docs.custom_fences import key_path

try:
    import kubernetes_validate.utils
except ImportError as e:
    raise ImportError(
        "the fence checks need kubernetes-validate, a dev dependency: run `poetry install --with dev`"
    ) from e

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
    """A value other than `value`: a bool flipped, a number plus one, an empty mapping or
    list with one entry, and any other value, null included, a string."""
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
        return [key_path(path) for (path, _), same in zip(leaves, pool.map(unchanged, leaves)) if same]


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
    unread = [
        f"`{path}`: the chart renders the same when it changes, so no template reads its value"
        for path in _unread(values, result.stdout, chart_dir)
    ]
    return unread + _schema_errors(result.stdout)
