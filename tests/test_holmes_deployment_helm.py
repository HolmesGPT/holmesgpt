"""Renders the Holmes Deployment with `helm template` and checks the container
command, including when the values predate the key (as under
`helm upgrade --reuse-values`, which reuses the previous chart's defaults)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not installed"
)

SERVER_COMMAND = ["python3", "-u", "server.py"]


def _holmes_container(*, set_json_args=()):
    args = ["helm", "template", "t", str(HELM_DIR), "--show-only", "templates/holmes.yaml"]
    for arg in set_json_args:
        args += ["--set-json", arg]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    deployment = next(
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc["kind"] == "Deployment"
    )
    containers = deployment["spec"]["template"]["spec"]["containers"]
    return next(c for c in containers if c["name"] == "holmes")


@pytest.mark.parametrize(
    "set_json_args", [[], ["command=null"]], ids=["defaults", "key-missing"]
)
def test_command_defaults_to_the_server(set_json_args):
    container = _holmes_container(set_json_args=set_json_args)

    assert container["command"] == SERVER_COMMAND


def test_command_can_be_overridden():
    command = ["python3", "-u", "experimental/ag-ui/server-agui.py"]

    container = _holmes_container(set_json_args=[f"command={json.dumps(command)}"])

    assert container["command"] == command
