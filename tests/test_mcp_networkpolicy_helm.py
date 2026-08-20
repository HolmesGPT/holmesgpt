"""Regression checks that every MCP server NetworkPolicy can select the Holmes pod.

The Holmes Deployment's pod template carries `app: holmes` and nothing else
(see templates/holmes.yaml), and `holmes.commonLabels` rejects the
`app.kubernetes.io/*` keys as reserved -- so a policy that tries to select
Holmes by `app.kubernetes.io/name` + `app.kubernetes.io/instance` can never
match, and silently denies *all* ingress to the MCP server it guards. The GCP
and Azure policies shipped that way; these tests keep it from coming back.

Text-based so they run without the `helm` binary, matching
test_kubernetes_remediation_helm.py.
"""

from pathlib import Path

import pytest
import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
MCP_DIR = HELM_DIR / "templates" / "mcp-servers"

NETPOLS = sorted(MCP_DIR.glob("*/networkpolicy.yaml"))
IDS = [p.parent.name for p in NETPOLS]

# A netpol whose namespace is operator-configurable can land outside the release
# namespace, where a bare podSelector no longer reaches Holmes.
CONFIGURABLE_NS_MARKER = "config.namespace | default .Release.Namespace"
RELEASE_NS_SELECTOR = "kubernetes.io/metadata.name: {{ .Release.Namespace }}"


def test_netpols_are_discovered():
    """Guard against the parametrized tests below passing vacuously."""
    assert NETPOLS, f"no networkpolicy.yaml templates found under {MCP_DIR}"


def test_holmes_pod_template_exposes_only_the_app_label():
    """`app: holmes` is the only label a netpol can select Holmes by."""
    text = (HELM_DIR / "templates" / "holmes.yaml").read_text()
    template = text.split("  template:", 1)[1]
    labels = template.split("annotations:", 1)[0]
    assert "app: holmes" in labels
    # No app.kubernetes.io/* identity labels are set on the pod.
    assert "app.kubernetes.io/name" not in labels
    assert "app.kubernetes.io/instance" not in labels


def test_commonlabels_reserves_the_app_kubernetes_io_keys():
    """Operators cannot add the missing labels themselves, so netpols must not want them."""
    text = (HELM_DIR / "templates" / "_helpers.tpl").read_text()
    reserved = text.split('define "holmes.commonLabels"', 1)[1].split("$reserved", 1)[1]
    guard = reserved.split("end")[0]
    assert '"app.kubernetes.io/name"' in guard
    assert '"app.kubernetes.io/instance"' in guard


@pytest.mark.parametrize("path", NETPOLS, ids=IDS)
def test_netpol_selects_holmes_by_app_label(path: Path):
    text = path.read_text()
    assert ("app: holmes" in text) is True, (
        f"{path.parent.name} netpol does not select Holmes by `app: holmes`; "
        "it will deny all ingress to the MCP server"
    )
    assert ("app.kubernetes.io/name: holmes" in text) is False, (
        f"{path.parent.name} netpol selects Holmes by app.kubernetes.io/name, "
        "which is never set on the Holmes pod"
    )


@pytest.mark.parametrize("path", NETPOLS, ids=IDS)
def test_netpol_in_configurable_namespace_pins_release_namespace(path: Path):
    """A bare podSelector only reaches pods in the policy's own namespace."""
    text = path.read_text()
    if CONFIGURABLE_NS_MARKER not in text:
        pytest.skip("netpol is pinned to the release namespace")
    lines = text.splitlines()
    idx = [i for i, line in enumerate(lines) if line.strip() == "app: holmes"]
    assert idx, f"{path.parent.name}: no `app: holmes` podSelector to check"
    for i in idx:
        window = "\n".join(lines[i : i + 5])
        assert (RELEASE_NS_SELECTOR in window) is True, (
            f"{path.parent.name} netpol can be installed into its own namespace but "
            "pairs `app: holmes` with no release-namespace selector, so Holmes is "
            "blocked whenever config.namespace is set"
        )


@pytest.mark.parametrize("path", NETPOLS, ids=IDS)
def test_netpol_template_is_wellformed_yaml_once_stripped(path: Path):
    """Catch indentation slips in the hand-edited ingress blocks."""
    stripped = [
        line
        for line in path.read_text().splitlines()
        if "{{" not in line and not line.lstrip().startswith("#")
    ]
    doc = yaml.safe_load("\n".join(stripped))
    assert doc["kind"] == "NetworkPolicy"
    from_rules = doc["spec"]["ingress"][0]["from"]
    holmes = [r for r in from_rules if r.get("podSelector", {}).get("matchLabels", {}).get("app") == "holmes"]
    assert len(holmes) == 1, f"{path.parent.name}: expected exactly one Holmes ingress rule"
