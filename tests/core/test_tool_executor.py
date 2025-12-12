from holmes.core.tools import ToolsetStatusEnum
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
