"""Holmes's own ServiceAccount must stay read-only (ROB-1926).

Renders the chart's Holmes RBAC and fails if its role grants anything beyond
get/list/watch, uses a wildcard, or reaches secrets, exec or proxy, or if
Holmes gets bound to any other role. namespaceScopedRBAC renders the same
rules as a Role, so the default render covers it. Opt-in write paths
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


def render_rbac(extra_args: List[str]) -> List[dict]:
    cmd = [
        "helm",
        "template",
        "test-release",
        str(HELM_DIR),
        "-s",
        "templates/holmesgpt-service-account.yaml",
    ] + extra_args
    output = subprocess.check_output(cmd, text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


@pytest.mark.parametrize("extra_args", [[], ["--set", "openshift=true"]], ids=["default", "openshift"])
def test_holmes_service_account_is_read_only(extra_args):
    docs = render_rbac(extra_args)
    roles = [d for d in docs if d["kind"] == "ClusterRole"]
    bindings = [d for d in docs if d["kind"] == "ClusterRoleBinding"]
    assert len(roles) == 1, f"expected one Holmes ClusterRole, got {len(roles)}"
    assert bindings, "expected the chart to bind the Holmes ClusterRole"

    for rule in roles[0]["rules"]:
        assert set(rule["verbs"]) <= READ_VERBS, f"non-read verbs: {rule}"
        assert "nonResourceURLs" not in rule, f"nonResourceURLs granted: {rule}"
        for field in ("apiGroups", "resources"):
            # Also catches partial wildcards such as "*/proxy".
            assert not any("*" in value for value in rule[field]), f"wildcard in {field}: {rule}"
        if "" in rule["apiGroups"]:
            banned = FORBIDDEN_CORE_RESOURCES & set(rule["resources"])
            assert not banned, f"grants {sorted(banned)}: {rule}"

    allowed_roles = {roles[0]["metadata"]["name"]} | ALLOWED_EXTERNAL_ROLES
    for binding in bindings:
        assert binding["roleRef"]["name"] in allowed_roles, f"Holmes bound to another role: {binding['roleRef']}"
