"""Regression checks for the hosted AWS MCP Server Helm wiring.

Holmes signs requests to the hosted AWS MCP Server itself, so the chart must not
render an AWS MCP pod any more and must emit one `mode: aws` server per account.
"""

from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
AWS_TEMPLATE_DIR = HELM_DIR / "templates" / "mcp-servers" / "aws"


def _values() -> dict:
    with open(HELM_DIR / "values.yaml") as f:
        return yaml.safe_load(f)["mcpAddons"]["aws"]


def test_values_have_no_image_or_pod_settings():
    v = _values()
    assert v["enabled"] is False
    assert v["config"]["region"] == "us-east-1"
    assert v["multiAccount"]["profiles"] == {}
    for removed in (
        "image",
        "registry",
        "serviceAccount",
        "networkPolicy",
        "resources",
    ):
        assert removed not in v
    assert "image" not in v["multiAccount"]


def test_no_aws_mcp_pod_is_rendered():
    assert sorted(p.name for p in AWS_TEMPLATE_DIR.iterdir()) == [
        "_helpers.tpl",
        "configmap.yaml",
    ]
    assert "kind: Deployment" not in (AWS_TEMPLATE_DIR / "configmap.yaml").read_text()


def test_servers_use_aws_mode_per_profile():
    helpers = (AWS_TEMPLATE_DIR / "_helpers.tpl").read_text()
    assert '"mode" "aws"' in helpers
    assert (
        "range $profile, $account := .Values.mcpAddons.aws.multiAccount.profiles"
        in helpers
    )
    assert "call_aws" not in helpers
    assert "aws___run_script" in helpers


def test_multi_account_config_uses_web_identity_profiles():
    configmap = (AWS_TEMPLATE_DIR / "configmap.yaml").read_text()
    assert (
        "web_identity_token_file = /var/run/secrets/eks.amazonaws.com/serviceaccount/token"
        in configmap
    )
    holmes = (HELM_DIR / "templates" / "holmes.yaml").read_text()
    assert "AWS_CONFIG_FILE" in holmes
    assert "audience: sts.amazonaws.com" in holmes
