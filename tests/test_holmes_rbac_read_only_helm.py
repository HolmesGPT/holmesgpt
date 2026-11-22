"""Holmes's own ServiceAccount must stay read-only (ROB-1926).

Renders the chart and fails if the role the chart binds to the Holmes
ServiceAccount grants anything beyond get/list/watch, or read access to
resources that act as write or credential access (secrets, exec, proxy).
Opt-in write paths (customClusterRoleRules, customServiceAccountName, the
kubernetesRemediation / kubernetes MCP addons, the operator) are out of scope:
they use other ServiceAccounts or are set explicitly by the user.
"""

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
RELEASE = "test-release"
HOLMES_SA = f"{RELEASE}-holmes-service-account"

READ_VERBS = {"get", "list", "watch"}

# Readable with get/create on these is equivalent to credential theft or
# running code in a workload, so they are banned even with read verbs.
FORBIDDEN_RESOURCES = {
    "secrets",
    "pods/exec",
    "pods/attach",
    "pods/portforward",
    "pods/proxy",
    "services/proxy",
    "nodes/proxy",
    "serviceaccounts/token",
}

# Read-only ClusterRoles the chart may bind besides its own role.
ALLOWED_EXTERNAL_ROLES = {"cluster-monitoring-view"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary not available")

# Every chart switch that changes the rendered Holmes RBAC.
RBAC_VARIANTS = {
    "default": [],
    "openshift": ["--set", "openshift=true"],
    "namespace-scoped": ["--set", "namespaceScopedRBAC=true"],
    "namespace-scoped-openshift": ["--set", "namespaceScopedRBAC=true", "--set", "openshift=true"],
}


def render(template: str, extra_args: Optional[List[str]] = None) -> List[dict]:
    cmd = ["helm", "template", RELEASE, str(HELM_DIR), "-s", template] + (extra_args or [])
    output = subprocess.check_output(cmd, text=True)
    return [doc for doc in yaml.safe_load_all(output) if doc]


def roles_and_bindings(extra_args: List[str]):
    docs = render("templates/holmesgpt-service-account.yaml", extra_args)
    roles = [d for d in docs if d["kind"] in ("ClusterRole", "Role")]
    bindings = [d for d in docs if d["kind"] in ("ClusterRoleBinding", "RoleBinding")]
    return roles, bindings


@pytest.fixture(params=list(RBAC_VARIANTS), ids=list(RBAC_VARIANTS))
def rbac(request):
    return roles_and_bindings(RBAC_VARIANTS[request.param])


def test_holmes_role_only_has_read_verbs(rbac):
    roles, _ = rbac
    assert roles, "expected the chart to render a Holmes role"
    for role in roles:
        for rule in role["rules"]:
            extra = set(rule["verbs"]) - READ_VERBS
            assert not extra, (
                f"{role['metadata']['name']} grants non-read verbs {sorted(extra)} "
                f"on {rule['resources']}; Holmes's ServiceAccount must stay read-only"
            )


def test_holmes_role_has_no_wildcards(rbac):
    roles, _ = rbac
    for role in roles:
        for rule in role["rules"]:
            for field in ("apiGroups", "resources", "verbs"):
                assert "*" not in rule.get(field, []), (
                    f"{role['metadata']['name']} uses a '*' wildcard in {field}: {rule}"
                )
            assert "nonResourceURLs" not in rule, (
                f"{role['metadata']['name']} grants nonResourceURLs: {rule}"
            )


def test_holmes_role_has_no_secret_exec_or_proxy_access(rbac):
    roles, _ = rbac
    for role in roles:
        for rule in role["rules"]:
            if "" not in rule["apiGroups"]:
                continue
            banned = FORBIDDEN_RESOURCES & set(rule["resources"])
            assert not banned, (
                f"{role['metadata']['name']} grants {sorted(banned)}; "
                "these give credential or in-container access"
            )


def test_holmes_bindings_only_reference_read_only_roles(rbac):
    roles, bindings = rbac
    own_roles = {r["metadata"]["name"] for r in roles}
    assert bindings, "expected the chart to bind the Holmes role"
    for binding in bindings:
        role_name = binding["roleRef"]["name"]
        assert role_name in own_roles | ALLOWED_EXTERNAL_ROLES, (
            f"{binding['metadata']['name']} binds Holmes to {role_name}, "
            "which is not the chart's read-only Holmes role"
        )
        assert [s["name"] for s in binding["subjects"]] == [HOLMES_SA]


def test_holmes_deployment_runs_as_the_read_only_service_account():
    docs = render("templates/holmes.yaml")
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == HOLMES_SA
