"""Regression checks for the GitHub MCP Helm wiring."""

from pathlib import Path


HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
TEMPLATE = HELM_DIR / "templates" / "mcp-servers" / "github" / "deployment.yaml"


def _template_text() -> str:
    return TEMPLATE.read_text()


def test_github_tools_allowlist_omits_toolsets():
    text = _template_text()
    data_block = text.split("data:", 1)[1].split("---\napiVersion: apps/v1", 1)[0]

    assert "{{- if $githubTools }}" in data_block
    assert "GITHUB_TOOLS: {{ $githubTools | quote }}" in data_block
    assert "{{- else if $githubToolsets }}" in data_block
    assert "GITHUB_TOOLSETS: {{ $githubToolsets | quote }}" in data_block
    assert "{{- if .Values.mcpAddons.github.config.toolsets }}" not in data_block

    assert "{{- if $githubToolsets }}\n        - name: GITHUB_TOOLSETS" in text
    assert "{{- if $githubTools }}\n        - name: GITHUB_TOOLS" in text


def test_github_app_command_forwards_tool_filters():
    text = _template_text()

    assert "- name: GITHUB_MCP_SERVER_CMD" in text
    assert (
        'value: {{ printf "github-mcp-server stdio %s %s" '
        "$githubFilterArg $githubFilterValue | trim | quote }}"
    ) in text
    assert '{{- $githubFilterArg = "--tools" }}' in text
    assert '{{- $githubFilterArg = "--toolsets" }}' in text


def test_github_pat_args_forward_tool_filters():
    text = _template_text()
    pat_args = text.split('# PAT path: native HTTP mode of github-mcp-server', 1)[1]
    pat_args = pat_args.split("{{- end }}", 1)[0]

    assert '{{- if $githubFilterArg }}' in pat_args
    assert "- {{ $githubFilterArg | quote }}" in pat_args
    assert "- {{ $githubFilterValue | quote }}" in pat_args
