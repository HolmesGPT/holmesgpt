import json
from typing import List, Optional

from holmes.core.tools import (
    ToolParameter,
    ToolsetStatusEnum,
    ToolsetType,
    YAMLTool,
    YAMLToolset,
)
from holmes.utils.toolset_inspect import (
    inspect_named_toolset,
    redact_secrets,
    resolve_toolset,
    serialize_toolset,
)


def _toolset(
    name: str,
    *,
    status: ToolsetStatusEnum = ToolsetStatusEnum.ENABLED,
    enabled: bool = True,
    error: Optional[str] = None,
    config: Optional[dict] = None,
    tools: Optional[List[YAMLTool]] = None,
    description: str = "demo toolset",
) -> YAMLToolset:
    return YAMLToolset(
        name=name,
        description=description,
        docs_url="https://holmesgpt.dev/data-sources/builtin-toolsets/helm/",
        enabled=enabled,
        status=status,
        type=ToolsetType.BUILTIN,
        error=error,
        config=config,
        llm_instructions="this must never appear in inspect output because it is huge",
        tools=tools
        or [
            YAMLTool(
                name="helm_values",
                description="Get Helm values",
                command="helm get values {{ release_name }} -n {{ namespace }}",
            )
        ],
    )


def test_resolve_exact_name():
    toolsets = [_toolset("helm/core"), _toolset("kubernetes/core")]
    match, suggestions, error = resolve_toolset(toolsets, "helm/core")
    assert error is None
    assert suggestions == []
    assert match is not None and match.name == "helm/core"


def test_resolve_case_insensitive():
    toolsets = [_toolset("helm/core")]
    match, _, error = resolve_toolset(toolsets, "HELM/CORE")
    assert error is None
    assert match is not None and match.name == "helm/core"


def test_resolve_unique_substring():
    toolsets = [_toolset("helm/core"), _toolset("kubernetes/core")]
    match, _, error = resolve_toolset(toolsets, "helm")
    assert error is None
    assert match is not None and match.name == "helm/core"


def test_resolve_ambiguous_kubernetes_prefix():
    toolsets = [
        _toolset("kubernetes/core"),
        _toolset("kubernetes/logs"),
        _toolset("helm/core"),
    ]
    match, suggestions, error = resolve_toolset(toolsets, "kubernetes")
    assert match is None
    assert error is not None and "Multiple" in error
    assert "kubernetes/core" in suggestions
    assert "kubernetes/logs" in suggestions
    assert "helm/core" not in suggestions


def test_resolve_unknown_returns_suggestions():
    toolsets = [_toolset("helm/core"), _toolset("internet")]
    match, suggestions, error = resolve_toolset(toolsets, "not-a-toolset")
    assert match is None
    assert error is not None and "Unknown" in error
    assert "helm/core" in suggestions


def test_resolve_empty_query():
    match, _, error = resolve_toolset([_toolset("helm/core")], "  ")
    assert match is None
    assert error == "Toolset name is required"


def test_redact_nested_secrets():
    raw = {
        "api_url": "https://example.invalid",
        "api_key": "super-secret",
        "headers": {"Authorization": "Bearer abc", "X-Request-Id": "1"},
        "empty_token": "",
    }
    redacted = redact_secrets(raw)
    assert redacted["api_url"] == "https://example.invalid"
    assert redacted["api_key"] == "***"
    assert redacted["headers"]["Authorization"] == "***"
    assert redacted["headers"]["X-Request-Id"] == "1"
    assert redacted["empty_token"] == ""


def test_serialize_omits_llm_instructions_and_redacts_config():
    toolset = _toolset(
        "prometheus/metrics",
        config={"prometheus_url": "http://localhost:9090", "api_key": "sekrit"},
        tools=[
            YAMLTool(
                name="execute_prometheus_instant_query",
                description="Run an instant query",
                command="echo {{ query }}",
                parameters={
                    "query": ToolParameter(
                        description="PromQL expression", type="string", required=True
                    )
                },
            )
        ],
    )
    payload = serialize_toolset(toolset)
    dumped = json.dumps(payload)
    assert "llm_instructions" not in payload
    assert "this must never appear" not in dumped
    assert "sekrit" not in dumped
    assert payload["config"]["api_key"] == "***"
    assert payload["config"]["prometheus_url"] == "http://localhost:9090"
    assert payload["tool_count"] == 1
    tool = payload["tools"][0]
    assert tool["name"] == "execute_prometheus_instant_query"
    assert "query" in tool["parameters"]
    assert tool["parameters"]["query"]["required"] is True


def test_serialize_truncates_long_command():
    long_cmd = "echo " + ("x" * 400)
    toolset = _toolset(
        "demo",
        tools=[YAMLTool(name="loud", description="loud", command=long_cmd)],
    )
    payload = serialize_toolset(toolset)
    command = payload["tools"][0]["command"]
    assert len(command) <= 200
    assert command.endswith("...")


def test_inspect_named_toolset_success_and_error():
    toolsets = [_toolset("helm/core"), _toolset("kubernetes/core")]
    payload, error = inspect_named_toolset(toolsets, "helm/core")
    assert error is None
    assert payload is not None
    assert payload["name"] == "helm/core"

    payload, error = inspect_named_toolset(
        toolsets + [_toolset("kubernetes/logs")], "kubernetes"
    )
    assert payload is None
    assert error is not None
    message, suggestions = error
    assert "Multiple" in message
    assert suggestions
