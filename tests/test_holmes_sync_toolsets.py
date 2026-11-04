import os
import subprocess
from unittest.mock import Mock, patch

import pytest
from mcp.types import Tool as MCP_Tool

from holmes.config import Config
from holmes.core.tools import (
    CallablePrerequisite,
    StaticPrerequisite,
    Toolset,
    ToolsetCommandPrerequisite,
    ToolsetEnvironmentPrerequisite,
    ToolsetStatusEnum,
    ToolParameter,
    ToolsetTag,
    YAMLTool,
)
from holmes.plugins.toolsets import load_builtin_toolsets
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool, RemoteMCPToolset
from holmes.plugins.toolsets.multi_instance import INSTANCE_PARAM
from holmes.utils.holmes_sync_toolsets import (
    build_remote_tools_meta,
    holmes_sync_toolsets_status,
    remote_tool_contract_hash,
)
from holmes.version import get_version
from tests.utils.bad_toolset_example import BadTool, BadToolset
from tests.utils.toolsets import callable_success, failing_callable_for_test


@pytest.fixture
def mock_dal():
    dal = Mock()
    dal.account_id = "test-account"
    dal.sync_toolsets = Mock()
    return dal


@pytest.fixture
def mock_config():
    config = Mock(spec=Config)
    config.cluster_name = "test-cluster"
    all_toolsets = load_builtin_toolsets(dal=None)
    config.create_server_tool_executor.return_value = Mock(toolsets=all_toolsets)
    return config


class SampleToolset(Toolset):
    pass


@pytest.fixture
def sample_toolset():
    return SampleToolset(
        name="test-toolset",
        description="Test toolset",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool", description="Test tool", command="echo test")
        ],
        tags=[ToolsetTag.CORE],
    )


def test_sync_toolsets_basic(mock_dal, mock_config, sample_toolset):
    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[sample_toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    call_args = mock_dal.sync_toolsets.call_args[0]

    assert len(call_args[0]) == 1
    toolset_data = call_args[0][0]

    assert toolset_data["toolset_name"] == "test-toolset"
    assert toolset_data["cluster_id"] == "test-cluster"
    assert toolset_data["account_id"] == "test-account"
    assert toolset_data["description"] == "Test toolset"
    assert toolset_data["status"] == ToolsetStatusEnum.DISABLED
    assert isinstance(toolset_data["updated_at"], str)


def test_sync_toolsets_no_cluster_name(mock_dal):
    config = Mock(spec=Config)
    config.cluster_name = None

    with pytest.raises(Exception) as exc_info:
        holmes_sync_toolsets_status(mock_dal, config)

    assert "Cluster name is missing" in str(exc_info.value)
    mock_dal.sync_toolsets.assert_not_called()


@patch("subprocess.run")
def test_sync_toolsets_with_config_schema(mock_subprocess_run, mock_dal, mock_config):
    mock_subprocess_run.return_value = Mock(stdout="success", returncode=0)

    # Create a toolset without config_classes - should have null schema
    toolset = SampleToolset(
        name="test-toolset",
        description="Test toolset",
        enabled=True,
        tools=[YAMLTool(name="tool1", description="Tool 1", command="echo test")],
        tags=[ToolsetTag.CORE],
    )
    toolset.check_prerequisites()
    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolset_data = mock_dal.sync_toolsets.call_args[0][0][0]
    assert toolset_data["installation_instructions"] is not None


@patch("subprocess.run")
def test_sync_toolsets_multiple(mock_subprocess_run, mock_dal, mock_config):
    mock_subprocess_run.return_value = Mock(stdout="success", returncode=0)

    toolset1 = SampleToolset(
        name="toolset1",
        description="First toolset",
        enabled=True,
        tools=[YAMLTool(name="tool1", description="Tool 1", command="echo 1")],
        tags=[ToolsetTag.CORE],
    )
    toolset1.check_prerequisites()

    toolset2 = SampleToolset(
        name="toolset2",
        description="Second toolset",
        enabled=False,
        tools=[YAMLTool(name="tool2", description="Tool 2", command="echo 2")],
        tags=[ToolsetTag.CLI],
        prerequisites=[
            StaticPrerequisite(enabled=False, disabled_reason="Feature flag disabled")
        ],
    )
    toolset2.check_prerequisites()

    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[toolset1, toolset2])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolsets_data = mock_dal.sync_toolsets.call_args[0][0]

    assert len(toolsets_data) == 2

    assert toolsets_data[0]["toolset_name"] == "toolset1"
    assert toolsets_data[0]["status"] == ToolsetStatusEnum.ENABLED

    assert toolsets_data[1]["toolset_name"] == "toolset2"
    assert toolsets_data[1]["status"] == ToolsetStatusEnum.FAILED


@patch("subprocess.run")
def test_sync_toolsets_with_prerequisites_check(
    mock_subprocess_run, mock_dal, mock_config
):
    mock_subprocess_run.return_value = Mock(stdout="success", returncode=0)

    toolset = SampleToolset(
        name="test-toolset",
        description="Test toolset",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool", description="Test tool", command="echo test")
        ],
        tags=[ToolsetTag.CORE],
        prerequisites=[],
    )
    toolset.check_prerequisites()

    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolset_data = mock_dal.sync_toolsets.call_args[0][0][0]

    assert toolset_data["status"] == ToolsetStatusEnum.ENABLED
    assert toolset_data["error"] is None


@patch("subprocess.run")
def test_sync_toolsets_with_failed_prerequisites(
    mock_subprocess_run, mock_dal, mock_config
):
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(
        1, "some-failing-command", "error output"
    )

    toolset = SampleToolset(
        name="test-toolset",
        description="Test toolset",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool", description="Test tool", command="echo test")
        ],
        tags=[ToolsetTag.CORE],
        prerequisites=[ToolsetCommandPrerequisite(command="some-failing-command")],
    )
    toolset.check_prerequisites()

    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolset_data = mock_dal.sync_toolsets.call_args[0][0][0]

    assert toolset_data["status"] == ToolsetStatusEnum.FAILED
    assert toolset_data["error"] is not None


@patch("subprocess.run")
def test_sync_toolsets_with_successful_prerequisites(
    mock_subprocess_run, mock_dal, mock_config
):
    mock_subprocess_run.return_value = Mock(stdout="success\n", returncode=0)

    toolset = SampleToolset(
        name="test-toolset",
        description="Test toolset",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool", description="Test tool", command="echo test")
        ],
        tags=[ToolsetTag.CORE],
        prerequisites=[
            ToolsetCommandPrerequisite(
                command="echo success", expected_output="success"
            )
        ],
    )
    toolset.check_prerequisites()

    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolset_data = mock_dal.sync_toolsets.call_args[0][0][0]

    assert toolset_data["status"] == ToolsetStatusEnum.ENABLED
    assert toolset_data["error"] is None


@patch.dict(os.environ, {}, clear=True)
def test_sync_toolsets_with_missing_env_var_prerequisites(mock_dal, mock_config):
    toolset = SampleToolset(
        name="test-toolset",
        description="Test toolset",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool", description="Test tool", command="echo test")
        ],
        tags=[ToolsetTag.CORE],
        prerequisites=[ToolsetEnvironmentPrerequisite(env=["NONEXISTENT_ENV_VAR"])],
    )
    toolset.check_prerequisites()

    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolset_data = mock_dal.sync_toolsets.call_args[0][0][0]

    assert toolset_data["status"] == ToolsetStatusEnum.FAILED
    assert toolset_data["error"] is not None
    assert "NONEXISTENT_ENV_VAR" in toolset_data["error"]


@patch("subprocess.run")
def test_sync_toolsets_with_command_output_mismatch(
    mock_subprocess_run, mock_dal, mock_config
):
    mock_subprocess_run.return_value = Mock(
        stdout="wrong output\n", returncode=0, stderr=""
    )

    toolset = SampleToolset(
        name="test-toolset",
        description="Test toolset",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool", description="Test tool", command="echo test")
        ],
        tags=[ToolsetTag.CORE],
        prerequisites=[
            ToolsetCommandPrerequisite(
                command="some-command", expected_output="expected output"
            )
        ],
    )
    toolset.check_prerequisites()

    mock_config.create_server_tool_executor.return_value = Mock(toolsets=[toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolset_data = mock_dal.sync_toolsets.call_args[0][0][0]

    assert toolset_data["status"] == ToolsetStatusEnum.FAILED
    assert toolset_data["error"] is not None


def test_sync_toolsets_with_toolset_having_failing_callable_prerequisite(
    mock_dal, mock_config
):
    toolset_with_failing_callable = SampleToolset(
        name="failing-callable-sync-toolset",
        description="Toolset with a callable prerequisite that raises an unhandled exception",
        enabled=True,
        tools=[
            YAMLTool(
                name="test-tool-fail", description="Test tool", command="echo test"
            )
        ],
        tags=[ToolsetTag.CORE],
        prerequisites=[
            CallablePrerequisite(callable=failing_callable_for_test)
        ],  # Using the imported callable
        config={},
    )

    successful_toolset_1 = SampleToolset(
        name="successful-toolset-1",
        description="A perfectly fine toolset",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool-ok-1", description="Test tool", command="echo ok1")
        ],
        tags=[ToolsetTag.CLUSTER],
        prerequisites=[],
        config={},
    )

    successful_toolset_2 = SampleToolset(
        name="successful-toolset-2",
        description="Another fine toolset with a passing callable",
        enabled=True,
        tools=[
            YAMLTool(name="test-tool-ok-2", description="Test tool", command="echo ok2")
        ],
        tags=[ToolsetTag.CORE],
        prerequisites=[CallablePrerequisite(callable=callable_success)],
        config={},
    )

    all_toolsets = [
        toolset_with_failing_callable,
        successful_toolset_1,
        successful_toolset_2,
    ]

    # We are mocking the Config class that's why we need to call check_prerequisites on each toolset here
    for ts in all_toolsets:
        ts.check_prerequisites()

    assert toolset_with_failing_callable.status == ToolsetStatusEnum.FAILED
    assert (
        "Prerequisite call failed unexpectedly: Failure in callable prerequisite"
        in toolset_with_failing_callable.error
    )
    assert successful_toolset_1.status == ToolsetStatusEnum.ENABLED
    assert successful_toolset_1.error is None
    assert successful_toolset_2.status == ToolsetStatusEnum.ENABLED
    assert successful_toolset_2.error is None

    mock_config.create_server_tool_executor.return_value = Mock(toolsets=all_toolsets)

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    call_args = mock_dal.sync_toolsets.call_args[0]

    synced_toolsets_data = call_args[0]
    assert len(synced_toolsets_data) == len(all_toolsets)

    toolsets_data = {data["toolset_name"]: data for data in synced_toolsets_data}

    failing_data = toolsets_data.get("failing-callable-sync-toolset")
    assert failing_data is not None
    assert failing_data["status"] == ToolsetStatusEnum.FAILED
    assert (
        "Prerequisite call failed unexpectedly: Failure in callable prerequisite"
        in failing_data["error"]
    )

    success1_data = toolsets_data.get("successful-toolset-1")
    assert success1_data is not None
    assert success1_data["status"] == ToolsetStatusEnum.ENABLED
    assert success1_data["error"] is None

    success2_data = toolsets_data.get("successful-toolset-2")
    assert success2_data is not None
    assert success2_data["status"] == ToolsetStatusEnum.ENABLED
    assert success2_data["error"] is None


def test_toolsets_dumpable(mock_dal, mock_config):
    """Test that all toolsets can be serialized via holmes_sync_toolsets_status.

    Validates production path: holmes_sync_toolsets_status line 53 (toolset.model_dump())
    -> ToolsetDBModel -> dal.sync_toolsets() for sending to database.
    """
    holmes_sync_toolsets_status(mock_dal, mock_config)


def _exposed_toolset(approval_required_tools=None):
    ts = SampleToolset(
        name="remote-toolset",
        description="Remotely exposed toolset",
        enabled=True,
        tools=[
            YAMLTool(name="list_buckets", description="List", command="echo a"),
            YAMLTool(name="delete_bucket", description="Delete", command="echo b"),
        ],
        tags=[ToolsetTag.CLUSTER],
        expose_remotely=True,
        approval_required_tools=approval_required_tools or [],
    )
    ts.check_prerequisites()
    assert ts.status == ToolsetStatusEnum.ENABLED
    return ts


def test_build_remote_tools_meta_publishes_yaml_tools():
    """Regression: build_remote_tools_meta must not call removed Tool._is_restricted().

    The restricted_tools mechanism was removed in favor of approval_required_tools;
    a stale reference here raised AttributeError on YAMLTool at toolset-sync time.
    """
    meta = build_remote_tools_meta(_exposed_toolset())

    assert meta is not None
    tool_names = {t["function"]["name"] for t in meta["tools"]}
    assert tool_names == {"list_buckets", "delete_bucket"}


def test_build_remote_tools_meta_excludes_approval_required_tools():
    meta = build_remote_tools_meta(
        _exposed_toolset(approval_required_tools=["delete_*"])
    )

    assert meta is not None
    tool_names = {t["function"]["name"] for t in meta["tools"]}
    assert tool_names == {"list_buckets"}


def test_build_remote_tools_meta_none_when_not_exposed():
    ts = _exposed_toolset()
    ts.expose_remotely = False

    assert build_remote_tools_meta(ts) is None


def _mock_get_server_tools(mock_tools_result):
    async def mock_get_server_tools():
        return mock_tools_result

    return mock_get_server_tools


def _create_mcp_toolset_with_tools(toolset_class):
    mcp_toolset = toolset_class(
        name="test-mcp-circular",
        description="Test MCP toolset",
        config={"url": "http://example.com/mcp", "mode": "sse"},
    )

    mock_tools_result = Mock()
    mock_tools_result.tools = [
        MCP_Tool(
            name="test_tool",
            description="Test tool",
            inputSchema={"type": "object", "properties": {}, "required": []},
        )
    ]

    mcp_toolset._get_server_tools = _mock_get_server_tools(mock_tools_result)
    success, error = mcp_toolset.prerequisites_callable(
        {"url": "http://example.com/mcp", "mode": "sse"}
    )

    assert success is True
    assert len(mcp_toolset.tools) > 0
    return mcp_toolset


def _create_bad_toolset():
    toolset = BadToolset(name="test-bad-toolset", description="Test bad toolset")
    mock_tool = MCP_Tool(
        name="test_tool",
        description="Test tool",
        inputSchema={"type": "object", "properties": {}, "required": []},
    )
    toolset.tools = [BadTool.create(mock_tool, toolset)]
    return toolset


def test_toolsets_dumpable_with_mcp_toolset_passes(mock_dal, mock_config):
    """Test that RemoteMCPToolset passes serialization with Field(exclude=True).

    Validates production path: holmes_sync_toolsets_status line 53 (toolset.model_dump())
    -> ToolsetDBModel -> dal.sync_toolsets() for sending to database.
    RemoteMCPToolset is not in builtin toolsets, it's loaded from config.
    """
    original_toolsets = list(mock_config.create_server_tool_executor.return_value.toolsets)

    mcp_toolset_good = _create_mcp_toolset_with_tools(RemoteMCPToolset)
    all_toolsets_good = list(original_toolsets)
    all_toolsets_good.append(mcp_toolset_good)
    mock_config.create_server_tool_executor.return_value.toolsets = all_toolsets_good

    holmes_sync_toolsets_status(mock_dal, mock_config)


# ---- ROB-1544: remote tool contract hashes ----


def _tool(parameters, description="d"):
    return YAMLTool(
        name="t", description=description, command="echo", parameters=parameters
    )


def _hash(parameters, description="d"):
    return remote_tool_contract_hash(_tool(parameters, description))


def test_contract_hash_ignores_descriptions():
    assert _hash(
        {"q": ToolParameter(type="string", description="a")}, description="old"
    ) == _hash({"q": ToolParameter(type="string", description="b")}, description="new")


def test_contract_hash_ignores_strict_mode():
    tool = _tool({"q": ToolParameter(type="string", required=False)})
    with patch("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", True):
        strict = (tool.get_openai_format(), remote_tool_contract_hash(tool))
    with patch("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", False):
        loose = (tool.get_openai_format(), remote_tool_contract_hash(tool))
    assert strict[0] != loose[0]
    assert strict[1] == loose[1]


@pytest.mark.parametrize(
    "changed",
    [
        # 0.40/0.41 published a phantom required `param` on the kubernetes tools.
        {"q": ToolParameter(type="string"), "param": ToolParameter(type="string")},
        {"q": ToolParameter(type="integer")},
        {"q": ToolParameter(type="string", required=False)},
        {"q": ToolParameter(type="string", enum=["a"])},
        {"q": ToolParameter(type="string", json_schema_extra={"maxLength": 3})},
    ],
)
def test_contract_hash_changes_with_what_a_caller_sends(changed):
    assert _hash({"q": ToolParameter(type="string")}) != _hash(changed)


def test_contract_hash_covers_nested_schemas_but_not_their_descriptions():
    def obj(inner_type, description):
        return {
            "o": ToolParameter(
                type="object",
                properties={
                    # A property may be named "description"; it is part of the contract.
                    "description": ToolParameter(type=inner_type, description=description)
                },
            )
        }

    assert _hash(obj("string", "x")) == _hash(obj("string", "y"))
    assert _hash(obj("string", "x")) != _hash(obj("integer", "x"))
    assert _hash(obj("string", "x")) != _hash({"o": ToolParameter(type="object")})


def test_contract_hash_leaves_out_the_injected_instance_param():
    assert _hash({"q": ToolParameter(type="string")}) == _hash(
        {"q": ToolParameter(type="string"), "instance": INSTANCE_PARAM}
    )


def test_contract_hash_counts_a_tools_own_instance_param():
    assert _hash({"q": ToolParameter(type="string")}) != _hash(
        {"q": ToolParameter(type="string"), "instance": ToolParameter(type="string")}
    )
    # Equal to the injected one, but the tool's own.
    assert _hash({"q": ToolParameter(type="string")}) != _hash(
        {"q": ToolParameter(type="string"), "instance": INSTANCE_PARAM.model_copy()}
    )


def test_contract_hash_keeps_literals_inside_raw_schemas():
    def const(value):
        return {"q": ToolParameter(type="object", json_schema_extra={"const": value})}

    assert _hash(const({"description": "a"})) != _hash(const({"description": "b"}))


def test_contract_hash_ignores_the_order_of_union_branches():
    a, b = ToolParameter(type="string"), ToolParameter(type="integer")
    assert _hash({"q": ToolParameter(any_of=[a, b])}) == _hash(
        {"q": ToolParameter(any_of=[b, a])}
    )

    def raw(branches):
        return {"q": ToolParameter(type="object", additional_properties={"anyOf": branches})}

    s, i = {"type": "string"}, {"type": "integer"}
    assert _hash(raw([s, i])) == _hash(raw([i, s]))
    assert _hash(raw([s, i])) != _hash(raw([s, {"type": "boolean"}]))


def test_contract_hash_ignores_annotations_inside_raw_schemas():
    def free_form(description, default):
        return {
            "labels": ToolParameter(
                type="object",
                additional_properties={
                    "type": "string",
                    "description": description,
                    "properties": {"description": {"type": "string", "title": description}},
                },
                json_schema_extra={"maxProperties": 5, "default": default},
            )
        }

    assert _hash(free_form("a", 1)) == _hash(free_form("b", 2))
    changed = free_form("a", 1)
    changed["labels"].additional_properties["properties"]["description"]["type"] = "integer"
    assert _hash(free_form("a", 1)) != _hash(changed)


def test_contract_hash_ignores_the_order_of_enum_values_and_union_types():
    assert _hash(
        {"q": ToolParameter(type=["string", "null"], enum=["a", "b", None])}
    ) == _hash({"q": ToolParameter(type=["null", "string"], enum=[None, "b", "a"])})


def test_contract_hash_keeps_the_primary_type_first():
    # The first non-null type is what the formatter publishes and what
    # coerce_params converts arguments to.
    assert _hash({"q": ToolParameter(type=["string", "integer"])}) != _hash(
        {"q": ToolParameter(type=["integer", "string"])}
    )
    assert _hash({"q": ToolParameter(type=["string", "integer", "boolean"])}) == _hash(
        {"q": ToolParameter(type=["string", "boolean", "integer"])}
    )
    assert _hash({"q": ToolParameter(type=["null", "string"])}) == _hash(
        {"q": ToolParameter(type=["string", "null"])}
    )


def test_contract_hash_treats_an_empty_enum_like_none():
    assert _hash({"q": ToolParameter(type="string", enum=[])}) == _hash(
        {"q": ToolParameter(type="string")}
    )


def test_contract_hash_treats_array_shorthand_like_items():
    assert _hash({"q": ToolParameter(type="array[string]")}) == _hash(
        {"q": ToolParameter(type="array", items=ToolParameter(type="string"))}
    )
    assert _hash({"q": ToolParameter(type="array[string]")}) != _hash(
        {"q": ToolParameter(type="array[integer]")}
    )


def test_contract_hash_treats_an_explicit_default_like_an_unset_field():
    assert _hash({"q": ToolParameter(type="string", required=True)}) == _hash(
        {"q": ToolParameter()}
    )


def test_remote_tools_meta_carries_a_contract_hash_per_tool():
    meta = build_remote_tools_meta(_exposed_toolset())
    assert all(len(t["contract_hash"]) == 64 for t in meta["tools"])


def test_sync_publishes_the_holmes_version_on_every_row(mock_dal, mock_config):
    holmes_sync_toolsets_status(mock_dal, mock_config)
    rows = mock_dal.sync_toolsets.call_args.args[0]
    assert rows and {r["version"] for r in rows} == {get_version()}


def test_contract_hash_ignores_a_json_schema_extra_holding_only_a_default():
    plain = _hash({"n": ToolParameter(type="integer")})
    with_default = _hash(
        {"n": ToolParameter(type="integer", json_schema_extra={"default": 1})}
    )
    assert plain == with_default


def test_contract_hash_tells_a_null_only_type_from_a_nullable_string():
    assert _hash({"x": ToolParameter(type=["null"])}) != _hash(
        {"x": ToolParameter(type=["string", "null"])}
    )


def test_contract_hash_reads_a_one_element_type_list_as_that_type():
    assert _hash({"x": ToolParameter(type=["string"])}) == _hash(
        {"x": ToolParameter(type="string")}
    )
    assert _hash({"x": ToolParameter(type=["integer"])}) == _hash(
        {"x": ToolParameter(type="integer")}
    )


def _mcp_tool(input_schema):
    return RemoteMCPTool.create(
        MCP_Tool(name="query", description="d", inputSchema=input_schema),
        toolset=RemoteMCPToolset(
            name="hash-mcp",
            description="d",
            config={"url": "http://example.com/mcp", "mode": "sse"},
        ),
    )


@pytest.mark.parametrize(
    "constraint",
    [
        {"format": "date-time"},
        {"uniqueItems": True},
        {"const": "x"},
        {"multipleOf": 5},
    ],
)
def test_contract_hash_of_an_mcp_tool_covers_its_full_input_schema(constraint):
    base = {"type": "object", "properties": {"q": {"type": "string"}}}
    constrained = {
        "type": "object",
        "properties": {"q": {"type": "string", **constraint}},
    }
    assert remote_tool_contract_hash(_mcp_tool(base)) != remote_tool_contract_hash(
        _mcp_tool(constrained)
    )


def test_contract_hash_of_an_mcp_tool_ignores_annotations():
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    annotated = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"q": {"type": "string", "description": "the query", "default": "a"}},
    }
    assert remote_tool_contract_hash(_mcp_tool(schema)) == remote_tool_contract_hash(
        _mcp_tool(annotated)
    )
