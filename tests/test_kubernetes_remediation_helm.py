"""Regression checks for the Kubernetes Remediation MCP Helm wiring.

These assert the chart values/templates encode the new approval-legible model
without needing the `helm` binary: the legacy restricted_tools mechanism is gone,
the scoped ClusterRole (no cluster-admin) is rendered, the NetworkPolicy is on,
the new config env vars are wired, and approval maps to run_kubectl_command.
"""

import re
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
    # run_kubectl_command mutates; run_gpu_node_host_diagnostics is node-root
    # (privileged + hostPID + host filesystem). Both must prompt a human.
    assert v["approvalRequiredTools"] == [
        "run_kubectl_command",
        "run_gpu_node_host_diagnostics",
    ]


def test_values_defaults_are_plug_and_play():
    v = _values()
    assert v["enabled"] is False  # opt-in
    # 1.3.0 carries the GPU node diagnostics tools; the gpuDiagnostics config
    # keys asserted below only take effect on that version or newer.
    assert v["image"] == "kubernetes-remediation-mcp:1.3.0"
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


def test_values_default_to_the_restrictive_diagnostic_target_policy():
    """The diagnostic target policy must ship on, and in-cluster-only, so a fresh
    install is not exposed to the ROB-910 SSRF/exfiltration path."""
    cfg = _values()["config"]
    assert cfg["diagnosticTargetPolicyEnabled"] is True
    assert cfg["diagnosticAllowExternalTargets"] is False
    assert cfg["diagnosticInternalDnsSuffixes"] == ".svc,.svc.cluster.local,.cluster.local"


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


def test_deployment_wires_diagnostic_target_policy_env():
    """Each target-policy key must appear twice: once in the ConfigMap data and
    once as a container env entry. A key present only in the ConfigMap is
    silently ignored by the server."""
    text = (TEMPLATE_DIR / "deployment.yaml").read_text()
    for key in (
        "KUBECTL_DIAGNOSTIC_TARGET_POLICY_ENABLED",
        "KUBECTL_DIAGNOSTIC_ALLOW_EXTERNAL_TARGETS",
        "KUBECTL_DIAGNOSTIC_INTERNAL_DNS_SUFFIXES",
    ):
        assert text.count(key) >= 2, f"{key} not wired through both ConfigMap and env"


def test_deployment_configmap_keys_all_reach_the_container():
    """Every KUBECTL_*/GPU_DIAG_* key defined in the ConfigMap block must also be
    referenced as an env var, so a newly added value cannot be dropped on the
    floor. Env references come in two template shapes: explicit
    `key: KUBECTL_...` entries and the quoted names in the `range $key := list`
    block that renders the GPU_DIAG_* entries."""
    import re

    text = (TEMPLATE_DIR / "deployment.yaml").read_text()
    defined = set(re.findall(r"^  ((?:KUBECTL|GPU_DIAG)_[A-Z_]+):", text, re.MULTILINE))
    referenced = set(re.findall(r"key: ((?:KUBECTL|GPU_DIAG)_[A-Z_]+)", text))
    referenced |= set(re.findall(r'"((?:KUBECTL|GPU_DIAG)_[A-Z_]+)"', text))
    assert defined, "no ConfigMap keys found — did the template layout change?"
    assert defined == referenced, (
        f"ConfigMap/env mismatch: only in ConfigMap={defined - referenced}, "
        f"only in env={referenced - defined}"
    )


def test_values_gpu_diagnostics_defaults():
    """GPU diagnostics ship enabled with the host tier available (it is
    approval-gated on the Holmes side) and the DCGM diag level capped at the
    quick, non-stressing run."""
    gpu = _values()["config"]["gpuDiagnostics"]
    assert gpu["enabled"] is True
    assert gpu["dcgmEnabled"] is True
    assert gpu["dcgmMaxDiagLevel"] == "1"
    assert gpu["runtimeClass"] == "nvidia"
    assert gpu["allowHostAccess"] is True
    for key in ("image", "dcgmImage", "hostImage", "timeout"):
        assert gpu[key], key
    # never a floating tag on a diagnostic image
    for key in ("image", "dcgmImage", "hostImage"):
        assert ":latest" not in gpu[key] and ":" in gpu[key], gpu[key]


def test_deployment_wires_gpu_diagnostics_env():
    text = (TEMPLATE_DIR / "deployment.yaml").read_text()
    for key in (
        "GPU_DIAG_ENABLED",
        "GPU_DIAG_IMAGE",
        "GPU_DIAG_DCGM_ENABLED",
        "GPU_DIAG_DCGM_IMAGE",
        "GPU_DIAG_DCGM_MAX_DIAG_LEVEL",
        "GPU_DIAG_RUNTIME_CLASS",
        "GPU_DIAG_ALLOW_HOST_ACCESS",
        "GPU_DIAG_HOST_IMAGE",
        "GPU_DIAG_TIMEOUT",
        "GPU_DIAG_NAMESPACE",
    ):
        assert text.count(key) >= 2, f"{key} not wired through both ConfigMap and env"
    # GPU pods run in the release namespace, where the operator applies the
    # diagnostic-pod egress NetworkPolicy
    assert "GPU_DIAG_NAMESPACE: {{ .Release.Namespace | quote }}" in text


def test_rbac_grants_pod_attach_for_diagnostic_pods():
    """kubectl run --rm -i (diagnostic-image and GPU-diagnostic pods) attaches to
    the pod to stream output; without pods/attach those tools fail."""
    text = (TEMPLATE_DIR / "rbac.yaml").read_text()
    assert "pods/attach" in text


def test_docs_inline_diagnostic_egress_policy_is_valid_and_restrictive():
    """The docs page carries the diagnostic-pod egress NetworkPolicy inline, because
    the chart does not install it (NetworkPolicy is namespaced and the namespace
    comes from the caller). Operators copy-paste it, so assert it stays parseable
    and actually restrictive: egress-only, selecting the label the server stamps,
    and never widened to 0.0.0.0/0 without the denied ranges carved back out."""
    doc = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "data-sources"
        / "builtin-toolsets"
        / "kubernetes-remediation-mcp.md"
    ).read_text()

    blocks = [
        b
        for b in re.findall(r"```yaml\n(.*?)```", doc, re.S)
        if "kubernetes-remediation-diagnostic-pod-egress" in b
    ]
    assert len(blocks) == 1, f"expected exactly one inlined policy, found {len(blocks)}"

    policy = yaml.safe_load(blocks[0])
    assert policy["kind"] == "NetworkPolicy"
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "robusta.dev/diagnostic-pod": "true"
    }

    rules = policy["spec"]["egress"]
    assert rules, "egress policy allows no destinations at all"

    # EVERY rule must be scoped by `to`. A rule carrying only `ports` matches all
    # destinations on those ports — that is how an "allow cluster DNS" rule
    # silently becomes "allow exfiltration over port 53 to any resolver".
    for i, rule in enumerate(rules):
        assert rule.get("to"), (
            f"egress rule {i} has no `to`, so it matches every destination on "
            f"{rule.get('ports', 'all ports')}"
        )

    # Only approved peer kinds, and only approved CIDRs.
    for i, rule in enumerate(rules):
        for peer in rule["to"]:
            assert set(peer) <= {"ipBlock", "namespaceSelector", "podSelector"}, (
                f"egress rule {i} has an unexpected peer kind: {sorted(peer)}"
            )

    cidrs = [
        peer["ipBlock"]["cidr"]
        for rule in rules
        for peer in rule["to"]
        if "ipBlock" in peer
    ]
    # RFC1918 only. 169.254.0.0/16, 127.0.0.0/8 etc. must be denied by omission.
    assert set(cidrs) == {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"}, cidrs

    # The DNS rule must be pinned to kube-dns rather than left open.
    dns_rules = [
        r
        for r in rules
        if any(p.get("port") == 53 for p in r.get("ports", []))
    ]
    assert len(dns_rules) == 1, "expected exactly one port-53 egress rule"
    assert any(
        "podSelector" in peer or "ipBlock" in peer for peer in dns_rules[0]["to"]
    ), "the DNS rule must name its destination"


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
    assert "run_gpu_node_diagnostics" in text
    assert "run_gpu_node_host_diagnostics" in text
