"""Renders the Holmes Deployment with `helm template` and checks the container
security context: the defaults, that user values are merged into them, and that
a field set to null is removed."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not installed"
)

DEFAULT_SECURITY_CONTEXT = {
    "readOnlyRootFilesystem": True,
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
    "seccompProfile": {"type": "RuntimeDefault"},
}


def _holmes_pod(*extra_args):
    args = [
        "helm",
        "template",
        "t",
        str(HELM_DIR),
        "--show-only",
        "templates/holmes.yaml",
        *extra_args,
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    deployment = next(
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc["kind"] == "Deployment"
    )
    return deployment["spec"]["template"]["spec"]


def _holmes_security_context(*extra_args):
    [container] = [
        c for c in _holmes_pod(*extra_args)["containers"] if c["name"] == "holmes"
    ]
    return container["securityContext"]


def test_holmes_container_has_a_restricted_security_context_by_default():
    assert _holmes_security_context() == DEFAULT_SECURITY_CONTEXT


def test_user_values_are_merged_into_the_defaults():
    security_context = _holmes_security_context(
        "--set-json", 'containerSecurityContext={"runAsNonRoot":true,"runAsUser":1000}'
    )
    assert security_context == {
        **DEFAULT_SECURITY_CONTEXT,
        "runAsNonRoot": True,
        "runAsUser": 1000,
    }


def test_user_values_override_the_defaults():
    security_context = _holmes_security_context(
        "--set",
        "containerSecurityContext.allowPrivilegeEscalation=true",
        "--set-json",
        'containerSecurityContext.capabilities={"add":["NET_RAW"]}',
    )
    assert security_context["allowPrivilegeEscalation"] is True
    assert security_context["capabilities"] == {"drop": ["ALL"], "add": ["NET_RAW"]}


def test_a_default_set_to_null_is_removed():
    security_context = _holmes_security_context(
        "--set", "containerSecurityContext.seccompProfile=null"
    )
    assert "seccompProfile" not in security_context
    assert security_context["allowPrivilegeEscalation"] is False
