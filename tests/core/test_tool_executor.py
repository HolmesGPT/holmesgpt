from unittest.mock import Mock

from holmes.core.tools import (
    CallablePrerequisite,
    ToolsetStatusEnum,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from tests.conftest import create_mock_tool_invoke_context
from tests.mocks.toolset_mocks import SampleToolset


def test_tool_executor_invoke_with_icon_url():
    toolset = SampleToolset(icon_url="https://example.com/icon.png")
    toolset.status = ToolsetStatusEnum.ENABLED
    tool_executor = ToolExecutor(toolsets=[toolset])
    tool = tool_executor.get_tool_by_name("dummy_tool")
    assert tool.icon_url == "https://example.com/icon.png"

    context = create_mock_tool_invoke_context()
    result = tool.invoke({}, context)
    assert result.icon_url == "https://example.com/icon.png"


def test_ensure_toolset_initialized_no_lazy_init():
    """For toolsets that are fully initialized, returns None (no error)."""
    toolset = SampleToolset()
    toolset.status = ToolsetStatusEnum.ENABLED
    tool_executor = ToolExecutor(toolsets=[toolset])
    result = tool_executor.ensure_toolset_initialized("dummy_tool")
    assert result is None


def test_ensure_toolset_initialized_triggers_lazy_init():
    """For lazily loaded toolsets, ensure_toolset_initialized triggers full prerequisites."""
    mock_callable = Mock(return_value=(True, ""))
    prereq = CallablePrerequisite(callable=mock_callable)
    toolset = SampleToolset(prerequisites=[prereq], config={"key": "val"})

    # Simulate the cached loading path: config check only
    toolset.check_config_prerequisites()
    toolset.status = ToolsetStatusEnum.ENABLED
    assert toolset.needs_initialization

    tool_executor = ToolExecutor(toolsets=[toolset])
    mock_callable.assert_not_called()

    # First tool use triggers lazy init
    result = tool_executor.ensure_toolset_initialized("dummy_tool")
    assert result is None
    mock_callable.assert_called_once_with({"key": "val"})
    assert toolset.status == ToolsetStatusEnum.ENABLED
    assert not toolset.needs_initialization


def test_ensure_toolset_initialized_failure():
    """If lazy init fails, returns an error message string."""
    mock_callable = Mock(return_value=(False, "Connection refused"))
    prereq = CallablePrerequisite(callable=mock_callable)
    toolset = SampleToolset(prerequisites=[prereq], config={})

    toolset.check_config_prerequisites()
    toolset.status = ToolsetStatusEnum.ENABLED

    tool_executor = ToolExecutor(toolsets=[toolset])
    result = tool_executor.ensure_toolset_initialized("dummy_tool")
    assert isinstance(result, str)
    assert "failed to initialize" in result.lower()
    assert "Connection refused" in result


def test_ensure_toolset_initialized_unknown_tool():
    """For unknown tool names, returns None (no error)."""
    toolset = SampleToolset()
    toolset.status = ToolsetStatusEnum.ENABLED
    tool_executor = ToolExecutor(toolsets=[toolset])
    result = tool_executor.ensure_toolset_initialized("nonexistent_tool")
    assert result is None
