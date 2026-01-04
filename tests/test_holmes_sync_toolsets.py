import os
import subprocess
from typing import Any, Dict
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
    ToolsetTag,
    YAMLTool,
)
from holmes.plugins.toolsets import load_builtin_toolsets
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPToolset
from holmes.utils.holmes_sync_toolsets import holmes_sync_toolsets_status
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
    config.create_tool_executor.return_value = Mock(toolsets=all_toolsets)
    return config


class SampleToolset(Toolset):
    def get_example_config(self) -> Dict[str, Any]:
        return {}


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
    mock_config.create_tool_executor.return_value = Mock(toolsets=[sample_toolset])

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


@patch(
    "holmes.utils.holmes_sync_toolsets.render_default_installation_instructions_for_toolset"
)
def test_sync_toolsets_with_installation_instructions(
    mock_render, mock_dal, mock_config, sample_toolset
):
    mock_render.return_value = "Test installation instructions"
    mock_config.create_tool_executor.return_value = Mock(toolsets=[sample_toolset])

    holmes_sync_toolsets_status(mock_dal, mock_config)

    mock_dal.sync_toolsets.assert_called_once()
    toolset_data = mock_dal.sync_toolsets.call_args[0][0][0]

    assert toolset_data["installation_instructions"] == "Test installation instructions"
    mock_render.assert_called_once_with(sample_toolset)


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

    mock_config.create_tool_executor.return_value = Mock(toolsets=[toolset1, toolset2])

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

    mock_config.create_tool_executor.return_value = Mock(toolsets=[toolset])

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

    mock_config.create_tool_executor.return_value = Mock(toolsets=[toolset])

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

    mock_config.create_tool_executor.return_value = Mock(toolsets=[toolset])

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

    mock_config.create_tool_executor.return_value = Mock(toolsets=[toolset])

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

    mock_config.create_tool_executor.return_value = Mock(toolsets=[toolset])

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

    mock_config.create_tool_executor.return_value = Mock(toolsets=all_toolsets)

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


def test_toolsets_dumpable_with_bad_toolset_fails(mock_dal, mock_config):
    """Test that BadToolset fails serialization due to circular reference.

    If this test fails unexpectedly (function succeeds when it should fail), it may indicate that:
    1. The code has changed and circular references are now handled differently
    2. The test is no longer valid for the current implementation
    3. The test should be updated or removed

    Validates production path: holmes_sync_toolsets_status line 53 (toolset.model_dump())
    should fail with circular reference error when Field(exclude=True) is missing.
    """
    original_toolsets = list(mock_config.create_tool_executor.return_value.toolsets)

    bad_toolset = _create_bad_toolset()
    all_toolsets_bad = list(original_toolsets)
    all_toolsets_bad.append(bad_toolset)
    mock_config.create_tool_executor.return_value.toolsets = all_toolsets_bad

    with pytest.raises(ValueError, match="Circular reference detected"):
        holmes_sync_toolsets_status(mock_dal, mock_config)


def test_toolsets_dumpable_with_mcp_toolset_passes(mock_dal, mock_config):
    """Test that RemoteMCPToolset passes serialization with Field(exclude=True).

    Validates production path: holmes_sync_toolsets_status line 53 (toolset.model_dump())
    -> ToolsetDBModel -> dal.sync_toolsets() for sending to database.
    RemoteMCPToolset is not in builtin toolsets, it's loaded from config.
    """
    original_toolsets = list(mock_config.create_tool_executor.return_value.toolsets)

    mcp_toolset_good = _create_mcp_toolset_with_tools(RemoteMCPToolset)
    all_toolsets_good = list(original_toolsets)
    all_toolsets_good.append(mcp_toolset_good)
    mock_config.create_tool_executor.return_value.toolsets = all_toolsets_good

    holmes_sync_toolsets_status(mock_dal, mock_config)
