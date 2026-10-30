import json
from typing import List, Optional

from rich.console import Console

from holmes.core.tools import (
    ToolParameter,
    ToolsetStatusEnum,
    ToolsetType,
    YAMLTool,
    YAMLToolset,
)
from holmes.utils.toolset_inspect import (
    inspect_named_toolset,
    redact_command,
    redact_secrets,
    render_toolset,
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


def test_redact_cloud_and_registry_credential_keys():
    raw = {
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "aws_secret_access_key": "example-secret",
        "DOCKER_AUTH_CONFIG": '{"auths": {}}',
        "region": "us-east-1",
    }
    redacted = redact_secrets(raw)
    assert redacted["AWS_ACCESS_KEY_ID"] == "***"
    assert redacted["aws_secret_access_key"] == "***"
    assert redacted["DOCKER_AUTH_CONFIG"] == "***"
    assert redacted["region"] == "us-east-1"


def test_redact_command_masks_credentials():
    cases = {
        "curl -H 'Authorization: Bearer example-token' https://x": "example-token",
        "curl -H 'X-Api-Key: abc123' https://x": "abc123",
        "mytool --password=hunter2 run": "hunter2",
        "mytool --token s3cr3t run": "s3cr3t",
        "AWS_SECRET_ACCESS_KEY=abcd aws s3 ls": "abcd",
        "psql postgres://admin:pa55@db:5432/app": "pa55",
        "curl -H 'Authorization: ApiKey abc123' https://x": "abc123",
        "curl -H 'X-Api-Key: \"abc123\"' https://x": "abc123",
        "git clone https://ghp_abc123@github.com/o/r": "ghp_abc123",
        "mytool --password={hunter2} run": "hunter2",
        "mytool --password='$hunter2' run": "hunter2",
    }
    for command, secret in cases.items():
        redacted = redact_command(command)
        assert secret not in redacted, redacted
        assert "***" in redacted


def test_redact_command_keeps_placeholders_and_plain_commands():
    plain = "helm get values {{ release_name }} -n {{ namespace }}"
    assert redact_command(plain) == plain
    templated = "curl -H 'Authorization: Bearer {{ token }}' https://x"
    assert redact_command(templated) == templated
    env = "mytool --token $API_TOKEN"
    assert redact_command(env) == env
    braced = 'mytool --token "${API_TOKEN}"'
    assert redact_command(braced) == braced
    url = "git clone https://$GH_TOKEN@github.com/o/r"
    assert redact_command(url) == url


def test_serialize_redacts_command_credentials():
    toolset = _toolset(
        "demo",
        tools=[
            YAMLTool(
                name="call",
                description="call api",
                command="curl -H 'Authorization: Bearer example-token' https://x",
            )
        ],
    )
    payload = serialize_toolset(toolset)
    assert "example-token" not in json.dumps(payload)


def test_render_shows_commands():
    console = Console(record=True, width=200)
    render_toolset(serialize_toolset(_toolset("helm/core")), console)
    output = console.export_text()
    assert "Command" in output
    assert "helm get values" in output


def test_render_escapes_command_markup():
    toolset = _toolset(
        "demo",
        tools=[
            YAMLTool(
                name="grep",
                description="grep logs",
                command="grep '[red]error[a-z]' /var/log/app.log",
            )
        ],
    )
    console = Console(record=True, width=200)
    render_toolset(serialize_toolset(toolset), console)
    assert "[red]error[a-z]" in console.export_text()
