"""Regression checks for the Azure MCP multi-account Helm wiring.

These assert the chart values/templates encode multi-account (multi-tenant)
support while staying backwards compatible, without needing the `helm` binary:
the single-account path is unchanged (workload-identity label + `--readonly`
args), and multi-account switches the image, mounts `accounts.yaml` plus a
projected federated token, and appends the multi-account LLM instructions.
"""

from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
TEMPLATE_DIR = HELM_DIR / "templates" / "mcp-servers" / "azure"


def _azure_values() -> dict:
    with open(HELM_DIR / "values.yaml") as f:
        return yaml.safe_load(f)["mcpAddons"]["azure"]


def test_multi_account_defaults_are_backwards_compatible():
    v = _azure_values()
    # Opt-in: default deployment behaves exactly as before.
    assert v["multiAccount"]["enabled"] is False
    assert v["multiAccount"]["image"] == "multi-azure-cli-mcp:1.1.0"
    assert v["multiAccount"]["accounts"] == {}
    # Single-account image/config still present and unchanged.
    assert v["image"] == "azure-cli-mcp:1.0.2"
    assert v["config"]["readOnlyMode"] is True
    assert v["config"]["authMethod"] == "workload-identity"


def test_deployment_single_account_path_unchanged():
    text = (TEMPLATE_DIR / "deployment.yaml").read_text()
    # Single-account args block (read-only guardrail) still rendered.
    assert '"--readonly"' in text
    # workload-identity label is gated so it only renders in single-account mode.
    assert (
        'if and (not .Values.mcpAddons.azure.multiAccount.enabled) '
        '(eq .Values.mcpAddons.azure.config.authMethod "workload-identity")' in text
    )
    # Single-account image selection is the fallback branch.
    assert "{{ .Values.mcpAddons.azure.registry }}/{{ .Values.mcpAddons.azure.image }}" in text


def test_deployment_multi_account_path_wired():
    text = (TEMPLATE_DIR / "deployment.yaml").read_text()
    # accounts.yaml projected into the ConfigMap from the accounts map.
    assert "accounts.yaml: |" in text
    assert "range $name, $config := .Values.mcpAddons.azure.multiAccount.accounts" in text
    # Wrapper env pointing at the mounted config + projected token.
    assert "AZURE_ACCOUNTS_FILE" in text
    assert "AZURE_FEDERATED_TOKEN_FILE" in text
    # Projected serviceAccountToken volume for federation.
    assert "serviceAccountToken:" in text
    assert "api://AzureADTokenExchange" in text
    # Multi-account image selection branch.
    assert "{{ .Values.mcpAddons.azure.registry }}/{{ .Values.mcpAddons.azure.multiAccount.image }}" in text


def test_helpers_append_multi_account_instructions():
    text = (TEMPLATE_DIR / "_helpers.tpl").read_text()
    assert "## Multiple Azure Accounts" in text
    # Gated on both the toggle and the descriptions being provided.
    assert (
        "if and .Values.mcpAddons.azure.multiAccount.enabled "
        ".Values.mcpAddons.azure.multiAccount.llm_account_descriptions" in text
    )
    assert "{{ .Values.mcpAddons.azure.multiAccount.llm_account_descriptions }}" in text
