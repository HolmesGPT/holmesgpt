import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from holmes.core.tools import ToolsetStatusEnum, ToolsetType, YAMLTool, YAMLToolset
from holmes.main import app

runner = CliRunner()


def _helm_toolset() -> YAMLToolset:
    return YAMLToolset(
        name="helm/core",
        description="Read access to cluster Helm charts and releases",
        docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/helm/",
        enabled=True,
        status=ToolsetStatusEnum.ENABLED,
        type=ToolsetType.BUILTIN,
        config={"api_key": "should-not-leak"},
        tools=[
            YAMLTool(
                name="helm_list",
                description="List Helm releases",
                command="helm list",
            ),
            YAMLTool(
                name="helm_values",
                description="Get Helm values",
                command="helm get values {{ release_name }} -n {{ namespace }}",
            ),
        ],
    )


def _invoke(args: list[str]):
    config = MagicMock()
    config.toolset_manager.list_console_toolsets.return_value = [
        _helm_toolset(),
        YAMLToolset(
            name="kubernetes/core",
            description="Kubernetes",
            enabled=True,
            status=ToolsetStatusEnum.ENABLED,
            type=ToolsetType.BUILTIN,
            tools=[
                YAMLTool(name="kubectl_get", description="get", command="kubectl get")
            ],
        ),
        YAMLToolset(
            name="kubernetes/logs",
            description="Kubernetes logs",
            enabled=True,
            status=ToolsetStatusEnum.ENABLED,
            type=ToolsetType.BUILTIN,
            tools=[
                YAMLTool(
                    name="fetch_pod_logs", description="logs", command="kubectl logs"
                )
            ],
        ),
    ]
    with patch("holmes.main.Config.load_from_file", return_value=config):
        return runner.invoke(app, args)


def test_toolset_inspect_help():
    result = runner.invoke(app, ["toolset", "inspect", "--help"])
    assert result.exit_code == 0, result.output
    assert "--json" in result.output
    assert (
        "redacted" in result.output.lower()
        or "Inspect" in result.output
        or "inspect" in result.output
    )


def test_toolset_inspect_unique_prefix():
    result = _invoke(["toolset", "inspect", "helm", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["name"] == "helm/core"


def test_toolset_inspect_table_shows_tools():
    result = _invoke(["toolset", "inspect", "helm/core"])
    assert result.exit_code == 0, result.output
    assert "helm/core" in result.output
    assert "helm_list" in result.output
    assert "helm_values" in result.output
    assert "release_name" in result.output
    assert "should-not-leak" not in result.output
    assert "***" in result.output


def test_toolset_inspect_json():
    result = _invoke(["toolset", "inspect", "helm/core", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["name"] == "helm/core"
    assert payload["tool_count"] == 2
    assert payload["config"]["api_key"] == "***"
    names = {tool["name"] for tool in payload["tools"]}
    assert names == {"helm_list", "helm_values"}
    assert "should-not-leak" not in result.output


def test_toolset_inspect_unknown_exits_one():
    result = _invoke(["toolset", "inspect", "not-a-toolset", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "error" in payload
    assert "suggestions" in payload


def test_toolset_inspect_ambiguous_prefix():
    result = _invoke(["toolset", "inspect", "kubernetes"])
    assert result.exit_code == 1, result.output
    assert "Multiple" in result.output
    assert "kubernetes/core" in result.output
    assert "kubernetes/logs" in result.output
