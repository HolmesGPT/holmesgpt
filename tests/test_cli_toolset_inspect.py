import json
import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from holmes.config import Config
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


def test_init_logging_can_route_to_stderr():
    import logging

    from rich.logging import RichHandler

    from holmes.utils.console.logging import init_logging

    init_logging([], log_to_stderr=True)
    handlers = [h for h in logging.getLogger().handlers if isinstance(h, RichHandler)]
    assert handlers and all(h.console.stderr for h in handlers)

    init_logging([])
    handlers = [h for h in logging.getLogger().handlers if isinstance(h, RichHandler)]
    assert handlers and not any(h.console.stderr for h in handlers)


def test_toolset_inspect_json_invalid_config_keeps_stdout_json():
    def _fail_load(*args, **kwargs):
        print("validation error details")
        sys.exit()

    with patch("holmes.main.Config.load_from_file", side_effect=_fail_load):
        result = runner.invoke(
            app, ["toolset", "inspect", "helm", "--json"], catch_exceptions=False
        )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"].startswith("Invalid config file")
    assert "validation error details" not in result.stdout


def test_toolset_inspect_json_malformed_config_emits_json_error(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: [unterminated\n")

    result = runner.invoke(
        app,
        ["toolset", "inspect", "helm", "--json", "--config", str(config_file)],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == f"Invalid config file at {config_file}"


def test_toolset_inspect_json_load_failure_does_not_blame_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4o\n")

    def _fail_load(*args, **kwargs):
        raise ValueError("bad entry api_key=should-not-leak")

    for args in ([], ["--config", str(config_file)]):
        with patch("holmes.main.Config.load_from_file", side_effect=_fail_load):
            result = runner.invoke(
                app,
                ["toolset", "inspect", "helm", "--json", *args],
                catch_exceptions=False,
            )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["error"].startswith("Failed to load settings")
        assert "Invalid config file" not in result.stdout
        assert "should-not-leak" not in result.stdout


def test_toolset_inspect_ignores_malformed_model_list(tmp_path, monkeypatch):
    model_list = tmp_path / "model_list.yaml"
    model_list.write_text("broken:\n  api_key: missing-the-model-field\n")
    monkeypatch.setattr("holmes.core.llm.MODEL_LIST_FILE_LOCATION", str(model_list))

    # Other commands still build the registry and report the bad entry.
    with pytest.raises(ValidationError):
        Config.load_from_file(None)

    with patch.object(
        Config, "toolset_manager", new_callable=PropertyMock
    ) as toolset_manager:
        toolset_manager.return_value.list_console_toolsets.return_value = [
            _helm_toolset()
        ]
        result = runner.invoke(
            app,
            [
                "toolset",
                "inspect",
                "helm",
                "--json",
                "--config",
                str(tmp_path / "absent.yaml"),
            ],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["name"] == "helm/core"


def _invoke_with_status_failure(args: list[str]):
    config = MagicMock()
    config.toolset_manager.list_console_toolsets.side_effect = ValueError(
        "bad toolset api_key=should-not-leak"
    )
    with patch("holmes.main.Config.load_from_file", return_value=config):
        return runner.invoke(app, args)


def test_toolset_inspect_json_status_failure_emits_json_error():
    result = _invoke_with_status_failure(["toolset", "inspect", "helm", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"].startswith("Failed to load toolset status")
    assert "should-not-leak" not in result.stdout


def test_toolset_inspect_status_failure_reraises_without_json():
    result = _invoke_with_status_failure(["toolset", "inspect", "helm"])
    assert isinstance(result.exception, ValueError)


def test_toolset_inspect_json_nonzero_config_exit_is_not_json():
    def _fail_load(*args, **kwargs):
        sys.exit(1)

    with patch("holmes.main.Config.load_from_file", side_effect=_fail_load):
        result = runner.invoke(
            app, ["toolset", "inspect", "helm", "--json"], catch_exceptions=False
        )
    assert result.exit_code == 1
    assert "Invalid config file" not in result.stdout


def test_toolset_inspect_renders_markup_like_values_literally():
    from rich.console import Console

    from holmes.utils.toolset_inspect import render_toolset

    console = Console(record=True, width=200)
    render_toolset(
        {
            "name": "bad[/bold]name",
            "status": "enabled",
            "enabled": True,
            "description": "uses [/nope] syntax",
            "error": "failed [/red] here",
            "config": {"pattern": "[/x]"},
            "tools": [
                {
                    "name": "tool[/i]",
                    "description": "lists [/b] items",
                    "parameters": {},
                }
            ],
        },
        console,
    )
    output = console.export_text()
    assert "bad[/bold]name" in output
    assert "uses [/nope] syntax" in output
    assert "failed [/red] here" in output
    assert "[/x]" in output
    assert "tool[/i]" in output
    assert "lists [/b] items" in output


def test_toolset_inspect_unknown_name_with_markup_exits_cleanly():
    result = _invoke(["toolset", "inspect", "[/bold]"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "[/bold]" in result.output
