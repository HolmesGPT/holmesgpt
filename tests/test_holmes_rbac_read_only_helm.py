"""Holmes's own ServiceAccount must stay read-only (ROB-1926).

Renders the whole chart and fails if the Holmes ClusterRole grants anything
beyond get/list/watch, uses a wildcard or an aggregationRule, or reaches
secrets, exec or proxy, or if any binding in the chart gives the Holmes
ServiceAccount another role. namespaceScopedRBAC renders the same rules as a
Role, so the default render covers it. Opt-in write paths
(customClusterRoleRules, customServiceAccountName, the kubernetesRemediation /
kubernetes MCP addons, the operator) are out of scope.
"""

import shutil
import subprocess
from pathlib import Path
from typing import List

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
RELEASE = "test-release"
HOLMES_ROLE = f"{RELEASE}-holmes-cluster-role"

READ_VERBS = {"get", "list", "watch"}

# Even read verbs on these give credential theft or in-container access.
FORBIDDEN_CORE_RESOURCES = {
    "secrets",
    "pods/exec",
    "pods/attach",
    "pods/portforward",
    "pods/proxy",
    "services/proxy",
    "nodes/proxy",
    "serviceaccounts/token",
}

# Read-only ClusterRole the chart also binds on OpenShift.
ALLOWED_EXTERNAL_ROLES = {"cluster-monitoring-view"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not available")


def render_chart(extra_args: List[str]) -> List[dict]:
    cmd = ["helm", "template", RELEASE, str(HELM_DIR), *extra_args]
    output = subprocess.check_output(cmd, text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


def binds_service_account(binding: dict, sa_name: str, namespace: str) -> bool:
    # A ServiceAccount is also its own User and a member of these two groups.
    matches = {
        ("ServiceAccount", sa_name),
        ("User", f"system:serviceaccount:{namespace}:{sa_name}"),
        ("Group", "system:serviceaccounts"),
        ("Group", f"system:serviceaccounts:{namespace}"),
    }
    return any((s["kind"], s["name"]) in matches for s in binding.get("subjects") or [])


@pytest.mark.parametrize("extra_args", [[], ["--set", "openshift=true"]], ids=["default", "openshift"])
def test_holmes_service_account_is_read_only(extra_args):
    docs = render_chart(extra_args)
    deployment = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == f"{RELEASE}-holmes")
    holmes_sa = deployment["spec"]["template"]["spec"]["serviceAccountName"]
    sa_doc = next(d for d in docs if d["kind"] == "ServiceAccount" and d["metadata"]["name"] == holmes_sa)
    namespace = sa_doc["metadata"]["namespace"]

    # Every binding in the chart that applies to Holmes, of either kind.
    bindings = [
        d
        for d in docs
        if d["kind"] in ("ClusterRoleBinding", "RoleBinding") and binds_service_account(d, holmes_sa, namespace)
    ]
    assert bindings, f"expected the chart to bind {holmes_sa}"
    for binding in bindings:
        role_ref = binding["roleRef"]
        assert role_ref["kind"] == "ClusterRole", f"Holmes bound to an unexpected role: {role_ref}"
        assert role_ref["name"] in {HOLMES_ROLE} | ALLOWED_EXTERNAL_ROLES, f"Holmes bound to another role: {role_ref}"

    role = next(d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"] == HOLMES_ROLE)
    # An aggregationRule lets the controller replace the rules below with other roles' permissions.
    assert "aggregationRule" not in role, f"{HOLMES_ROLE} uses an aggregationRule: {role['aggregationRule']}"
    for rule in role["rules"]:
        assert set(rule["verbs"]) <= READ_VERBS, f"non-read verbs: {rule}"
        assert "nonResourceURLs" not in rule, f"nonResourceURLs granted: {rule}"
        for field in ("apiGroups", "resources"):
            # Also catches partial wildcards such as "*/proxy".
            assert not any("*" in value for value in rule[field]), f"wildcard in {field}: {rule}"
        if "" in rule["apiGroups"]:
            banned = FORBIDDEN_CORE_RESOURCES & set(rule["resources"])
            assert not banned, f"grants {sorted(banned)}: {rule}"
