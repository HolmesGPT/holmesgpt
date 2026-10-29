"""Renders the Holmes chart with `helm template` and checks that values feeding
the custom-toolsets-configmap roll the Holmes pod through its
checksum/toolset-config annotation, whichever order Helm renders the two
templates in."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not installed"
)


@pytest.fixture(params=["as-shipped", "configmap-rendered-last"])
def chart(request, tmp_path):
    if request.param == "as-shipped":
        return HELM_DIR
    # Helm renders same-directory templates in reverse lexical order, so this
    # name renders the configmap after holmes.yaml.
    chart = tmp_path / "holmes"
    shutil.copytree(HELM_DIR, chart)
    templates = chart / "templates"
    (templates / "toolset-config.yaml").rename(templates / "a-toolset-config.yaml")
    holmes = templates / "holmes.yaml"
    holmes.write_text(
        holmes.read_text().replace('"/toolset-config.yaml"', '"/a-toolset-config.yaml"')
    )
    return chart


def _checksum(chart, *set_args):
    args = ["helm", "template", "t", str(chart), "--show-only", "templates/holmes.yaml"]
    for arg in set_args:
        args += ["--set", arg]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    deployment = next(
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc["kind"] == "Deployment"
    )
    return deployment["spec"]["template"]["metadata"]["annotations"][
        "checksum/toolset-config"
    ]


@pytest.mark.parametrize(
    "set_args",
    [
        ["toolsets.prometheus/metrics.enabled=false"],
        ["mcp_servers.example.config.url=http://example:8000/mcp"],
        ["modelList.example.model=gpt-4.1"],
        ["k8sRBAC=true"],
        ["mcpAddons.aws.enabled=true"],
        ["mcpAddons.gcp.enabled=true"],
        ["mcpAddons.azure.enabled=true"],
    ],
    ids=lambda set_args: set_args[0].split("=")[0],
)
def test_toolset_configmap_inputs_roll_the_pod(chart, set_args):
    assert _checksum(chart, *set_args) != _checksum(chart)


def test_checksum_is_stable_across_renders(chart):
    # Random content in the configmap (e.g. a generated token) would roll the pod on every upgrade.
    assert _checksum(chart, "k8sRBAC=true") == _checksum(chart, "k8sRBAC=true")
