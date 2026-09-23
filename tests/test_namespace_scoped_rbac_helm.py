"""Regression checks for the namespaceScopedRBAC Helm mode.

With namespaceScopedRBAC=true the chart renders the Holmes RBAC as a namespaced
Role + RoleBinding (same rules) instead of a ClusterRole + ClusterRoleBinding,
and skips the OpenShift cluster-monitoring ClusterRoleBinding. The default
(false) keeps the cluster-wide objects unchanged.
"""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm binary not available"
)


def render_rbac(extra_args: Optional[List[str]] = None) -> List[dict]:
    cmd = [
        "helm",
        "template",
        "test-release",
        str(HELM_DIR),
        "-s",
        "templates/holmesgpt-service-account.yaml",
    ] + (extra_args or [])
    output = subprocess.check_output(cmd, text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


def get_doc(docs: List[dict], kind: str) -> Optional[dict]:
    for doc in docs:
        if doc["kind"] == kind:
            return doc
    return None


def test_default_renders_cluster_wide_rbac():
    docs = render_rbac()
    assert get_doc(docs, "ClusterRole") is not None
    assert get_doc(docs, "ClusterRoleBinding") is not None
    assert get_doc(docs, "ServiceAccount") is not None
    assert get_doc(docs, "Role") is None
    assert get_doc(docs, "RoleBinding") is None


def test_namespace_scoped_renders_role_and_rolebinding():
    docs = render_rbac(["--set", "namespaceScopedRBAC=true"])

    assert get_doc(docs, "ClusterRole") is None
    assert get_doc(docs, "ClusterRoleBinding") is None

    role = get_doc(docs, "Role")
    assert role is not None
    assert role["metadata"]["name"] == "test-release-holmes-role"
    assert role["metadata"]["namespace"] == "default"
    # same rule set as the ClusterRole - spot-check a core rule survived the kind switch
    assert any(
        "pods" in rule.get("resources", []) and "list" in rule.get("verbs", [])
        for rule in role["rules"]
    )

    binding = get_doc(docs, "RoleBinding")
    assert binding is not None
    assert binding["metadata"]["namespace"] == "default"
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": "test-release-holmes-role",
    }
    assert binding["subjects"][0]["name"] == "test-release-holmes-service-account"


def test_namespace_scoped_skips_openshift_cluster_monitoring_binding():
    docs = render_rbac(["--set", "namespaceScopedRBAC=true", "--set", "openshift=true"])
    assert get_doc(docs, "ClusterRoleBinding") is None


def render_deployment(extra_args: Optional[List[str]] = None) -> dict:
    cmd = [
        "helm",
        "template",
        "test-release",
        str(HELM_DIR),
        "-s",
        "templates/holmes.yaml",
    ] + (extra_args or [])
    output = subprocess.check_output(cmd, text=True)
    docs = [doc for doc in yaml.safe_load_all(output) if doc]
    deployment = get_doc(docs, "Deployment")
    assert deployment is not None
    return deployment


def _env_map(deployment: dict) -> dict:
    env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    return {e["name"]: e.get("value") for e in env}


def test_namespace_scoped_sets_scoped_namespaces_env():
    env = _env_map(render_deployment(["--set", "namespaceScopedRBAC=true"]))
    assert env["SCOPED_NAMESPACES"] == "default"


def test_default_has_no_scoped_namespaces_env():
    env = _env_map(render_deployment())
    assert "SCOPED_NAMESPACES" not in env
