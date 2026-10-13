"""Regression checks for the hosted AWS MCP Server Helm wiring.

With `hosted.enabled` Holmes signs requests to the hosted AWS MCP Server itself:
no AWS MCP pod is rendered and one `mode: aws` server is emitted per account.
Without it the chart keeps deploying the legacy aws-api-mcp-server pod unchanged.
"""

from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
AWS_TEMPLATE_DIR = HELM_DIR / "templates" / "mcp-servers" / "aws"


def _values() -> dict:
    with open(HELM_DIR / "values.yaml") as f:
        return yaml.safe_load(f)["mcpAddons"]["aws"]


def test_hosted_mode_is_opt_in_and_legacy_values_are_kept():
    v = _values()
    assert v["enabled"] is False
    assert v["hosted"] == {"enabled": False, "profile": ""}
    assert v["image"] == "aws-api-mcp-server:2.1.0"
    assert v["multiAccount"]["image"] == "multi-aws-api-mcp-server:2.1.0"
    assert v["serviceAccount"]["name"] == "aws-api-mcp-sa"


def test_legacy_pod_is_gated_off_in_hosted_mode():
    for name in ("deployment.yaml", "networkpolicy.yaml"):
        assert (
            "(not .Values.mcpAddons.aws.hosted.enabled)"
            in (AWS_TEMPLATE_DIR / name).read_text()
        )


def test_servers_use_aws_mode_per_profile():
    helpers = (AWS_TEMPLATE_DIR / "_helpers.tpl").read_text()
    assert '"mode" "aws"' in helpers
    assert (
        "range $profile, $account := .Values.mcpAddons.aws.multiAccount.profiles"
        in helpers
    )
    assert "aws___run_script" in helpers
    assert (
        "h-aws-mcp-server" not in helpers
        and "-aws-mcp-server.%s.svc.cluster.local" in helpers
    )


def test_multi_account_config_uses_web_identity_profiles():
    configmap = (AWS_TEMPLATE_DIR / "configmap.yaml").read_text()
    assert (
        "web_identity_token_file = /var/run/secrets/eks.amazonaws.com/serviceaccount/token"
        in configmap
    )
    holmes = (HELM_DIR / "templates" / "holmes.yaml").read_text()
    assert "AWS_CONFIG_FILE" in holmes
    assert "audience: sts.amazonaws.com" in holmes
