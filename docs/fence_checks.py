"""
The checks of a deployment fence's Helm values against what reads them: the Holmes chart, and the
Kubernetes API for what its templates pass through into an object. tests/docs runs `check_fence`
on the values of every Holmes Helm Chart tab; the MkDocs build does not run it, and needs neither
Helm nor this module.

The chart passes `toolsets`, `mcp_servers` and `modelList` on to Holmes in a ConfigMap, so the keys
under them are Holmes's, not the chart's; what Holmes does with them is not checked here.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterator, List

import yaml  # type: ignore

from docs.custom_fences import empty_value, key_path

try:
    import kubernetes_validate.utils
except ImportError as e:
    raise ImportError(
        "the fence checks need kubernetes-validate, a dev dependency: run `poetry install --with dev`"
    ) from e

# The Kubernetes version whose API schemas, as kubernetes-validate bundles them, the rendered
# objects are checked against.
KUBERNETES_VERSION = "1.36"

# The values the chart passes on to Holmes, which reads the keys under them.
PASSED_TO_HOLMES = ("toolsets", "mcp_servers", "modelList")


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


def chart_defaults(chart_dir: Path) -> dict:
    """The chart's `values.yaml`, with each value it passes on to Holmes free-form."""
    defaults = yaml.safe_load((chart_dir / "values.yaml").read_text()) or {}
    return {**defaults, **dict.fromkeys(PASSED_TO_HOLMES, {})}


def unresolved_key_paths(values, defaults, path: tuple = ()) -> Iterator[tuple]:
    """Each key path of `values` that `defaults`, a chart's values, does not declare, at any
    depth. Under a key whose default is an empty mapping or list, or null, any key resolves;
    a list entry resolves against the first entry of its default."""
    if defaults is None or defaults == {} or defaults == []:
        return
    if isinstance(values, dict):
        for key, value in values.items():
            if isinstance(defaults, dict) and key in defaults:
                yield from unresolved_key_paths(value, defaults[key], path + (key,))
            else:
                yield path + (key,)
    elif isinstance(values, list):
        entry = defaults[0] if isinstance(defaults, list) else defaults
        for index, value in enumerate(values):
            yield from unresolved_key_paths(value, entry, path + (index,))


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

    No value may be empty (a list entry `""` excepted), as on the pages. Every key path must
    be one the chart's `values.yaml` declares (`unresolved_key_paths`); the values must render
    once with `helm template`, and every rendered object must match its Kubernetes schema."""
    empty = empty_value(values)
    if empty:
        return [f"`{key_path(empty)}` has no value"]
    errors = [
        f"`{key_path(path)}` is not a key path of the chart's values.yaml"
        for path in unresolved_key_paths(values, chart_defaults(chart_dir))
    ]
    result = _render(values, chart_dir)
    if result.returncode != 0:
        return errors + [f"helm template fails: {result.stderr.strip()}"]
    return errors + _schema_errors(result.stdout)
