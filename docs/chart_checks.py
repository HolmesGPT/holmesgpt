"""
The checks of Helm values against a chart and the Kubernetes API: every leaf of the values must
change the chart's render when it changes, and every rendered object must match its Kubernetes
schema. `check_chart` takes any chart directory, values and release name, so a caller with
another chart runs it too; `docs/fence_checks.py` runs it on each Holmes Helm Chart tab and adds
Holmes's own checks. This module imports nothing from `holmes`: it needs Helm on PATH,
kubernetes-validate, PyYAML and `docs/custom_fences.py`.
"""

import copy
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional, Set, Tuple

import yaml  # type: ignore

from docs.custom_fences import key_path

try:
    import kubernetes_validate.utils
except ImportError as e:
    raise ImportError(
        "the chart checks need kubernetes-validate, a dev dependency: run `poetry install --with dev`"
    ) from e

# The Kubernetes version whose API schemas, as kubernetes-validate bundles them, the rendered
# objects are checked against.
KUBERNETES_VERSION = "1.36"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        raise RuntimeError(
            "the chart checks need Helm on PATH: install Helm 3 or later, https://helm.sh/docs/intro/install/"
        )
    return helm


def _render(chart_dir: Path, values: dict, release: str) -> subprocess.CompletedProcess:
    """`helm template` of the chart with `values`, as the release `release`."""
    return subprocess.run(
        [_helm(), "template", release, str(chart_dir), "-f", "-"],
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
    items included. An empty mapping or list has no leaf, so nothing here checks it."""
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


def _unread(chart_dir: Path, values: dict, release: str, render: str) -> List[str]:
    """An error for each leaf of `values` whose change leaves the chart's `render` the same.
    A change Helm refuses, or one that changes the number of lines, counts as read."""
    leaves = list(_leaves(values))
    if not leaves:
        return []
    base = _lines(render)

    def differing(leaf) -> Optional[Set[int]]:
        path, value = leaf
        result = _render(chart_dir, _with(values, path, _changed(value)), release)
        lines = _lines(result.stdout)
        if result.returncode != 0 or len(lines) != len(base):
            return None
        return _differing(base, lines)

    with ThreadPoolExecutor() as pool:
        changes = list(pool.map(differing, leaves))
    # Rendered after every change, so it differs from `base` at each line holding a random value,
    # or a time that moved on while the changes rendered: a change there says nothing about a value.
    again = _lines(_render(chart_dir, values, release).stdout)
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


class ChartCheck(NamedTuple):
    # `helm template`'s output, or None when Helm refuses the values.
    render: Optional[str]
    errors: List[str]


def check_chart(chart_dir: Path, values: dict, release: str) -> ChartCheck:
    """The render of the chart in `chart_dir` with `values` as the release `release`, and what
    the chart and the Kubernetes API refuse in the values. The values must render with
    `helm template`, every leaf must change the render when it changes (else the render does
    not depend on its value), and every rendered object must match its Kubernetes schema. A
    line that differs between two renders of `values`, such as a random token or the time, is
    not counted as a change."""
    result = _render(chart_dir, values, release)
    if result.returncode != 0:
        return ChartCheck(None, [f"helm template fails: {result.stderr.strip()}"])
    return ChartCheck(
        result.stdout,
        _unread(chart_dir, values, release, result.stdout) + _schema_errors(result.stdout),
    )
