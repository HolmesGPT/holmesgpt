"""Regression checks for the Kubernetes Remediation MCP Helm wiring.

These assert the chart values/templates encode the new approval-legible model
without needing the `helm` binary: the legacy restricted_tools mechanism is gone,
the scoped ClusterRole (no cluster-admin) is rendered, the NetworkPolicy is on,
the new config env vars are wired, and approval maps to run_kubectl_command.
"""

from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
TEMPLATE_DIR = HELM_DIR / "templates" / "mcp-servers" / "kubernetes-remediation"


def _values() -> dict:
    with open(HELM_DIR / "values.yaml") as f:
        return yaml.safe_load(f)["mcpAddons"]["kubernetesRemediation"]


def test_values_drop_restricted_tools_and_map_approval():
    v = _values()
    assert "restrictedTools" not in v
    assert v["approvalRequiredTools"] == ["run_kubectl_command"]


def test_values_defaults_are_plug_and_play():
    v = _values()
    assert v["enabled"] is False  # opt-in
    assert v["image"] == "kubernetes-remediation-mcp:1.1.0"
    assert v["serviceAccount"]["clusterRole"] == ""  # chart creates scoped role
    assert v["networkPolicy"]["enabled"] is True
    assert v["config"]["allowArbitraryKubectlCommands"] is True
    # New config keys present
    for key in (
        "preapprovedCommands",
        "diagnosticImages",
        "fileReadAllowedPaths",
        "fileReadDeniedPaths",
    ):
        assert key in v["config"], key
    # Old run_image image allowlist is gone
    assert "allowedImages" not in v["config"]


def test_rbac_template_is_scoped_not_cluster_admin():
    text = (TEMPLATE_DIR / "rbac.yaml").read_text()
    assert "cluster-admin" not in text
    # secrets must NOT be granted (defense in depth)
    assert "secrets" not in text
    # gated on create AND empty clusterRole
    assert "serviceAccount.create" in text
    assert "not .Values.mcpAddons.kubernetesRemediation.serviceAccount.clusterRole" in text
    # representative scoped rules
    assert "pods/eviction" in text
    assert "deployments/scale" in text


def test_deployment_binding_has_no_cluster_admin_default():
    text = (TEMPLATE_DIR / "deployment.yaml").read_text()
    assert 'default "cluster-admin"' not in text
    assert "k8s-remediation-mcp-role" in text
    # new env vars wired through the ConfigMap
    for key in (
        "KUBECTL_PREAPPROVED_COMMANDS",
        "KUBECTL_DIAGNOSTIC_IMAGES",
        "KUBECTL_FILE_READ_ALLOWED_PATHS",
        "KUBECTL_FILE_READ_DENIED_PATHS",
        "KUBECTL_ALLOW_ARBITRARY_COMMANDS",
    ):
        assert key in text, key
    assert "KUBECTL_ALLOWED_IMAGES" not in text


def test_networkpolicy_is_ingress_only_and_scoped():
    text = (TEMPLATE_DIR / "networkpolicy.yaml").read_text()
    assert "Ingress" in text
    assert "egress" not in text.lower()
    assert "app: holmes" in text
    assert "kubernetes.io/metadata.name" in text  # release-namespace selector


def test_toolset_config_emits_approval_not_restricted():
    text = (HELM_DIR / "templates" / "toolset-config.yaml").read_text()
    # Find the k8s remediation block
    assert "kubernetes_remediation" in text
    assert "restricted_tools" not in text
    assert "approvalRequiredTools" in text


def test_llm_instructions_mention_the_tool_split():
    text = (TEMPLATE_DIR / "_helpers.tpl").read_text()
    assert "run_kubectl_command" in text
    assert "read_file_from_container" in text
    assert "run_preapproved_diagnostic_image" in text
    assert "run_preapproved_kubectl_command" in text
