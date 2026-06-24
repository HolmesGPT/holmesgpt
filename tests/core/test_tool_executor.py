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


def test_ensure_toolset_initialized_failure_blocks_subsequent_calls():
    """After lazy init fails, subsequent calls must still return an error.

    Regression test: previously, the second call would see needs_initialization=False
    (because _initialized was set to True after the first attempt) and return None,
    allowing tool execution against a FAILED toolset.
    """
    mock_callable = Mock(return_value=(False, "Connection refused"))
    prereq = CallablePrerequisite(callable=mock_callable)
    toolset = SampleToolset(prerequisites=[prereq], config={})

    toolset.check_config_prerequisites()
    toolset.status = ToolsetStatusEnum.ENABLED

    tool_executor = ToolExecutor(toolsets=[toolset])

    # First call: triggers lazy init, which fails
    result1 = tool_executor.ensure_toolset_initialized("dummy_tool")
    assert isinstance(result1, str)
    assert "Connection refused" in result1

    # Second call: must still return an error, not None
    result2 = tool_executor.ensure_toolset_initialized("dummy_tool")
    assert isinstance(result2, str)
    assert "unavailable" in result2.lower()
    assert "Connection refused" in result2

    # The callable should only have been invoked once (lazy init is not retried)
    mock_callable.assert_called_once()


# ── Tool search (progressive disclosure) ─────────────────────────────────────

from holmes.core.tool_search import LOAD_TOOLS_NAME  # noqa: E402
from holmes.core.tools import (  # noqa: E402
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetType,
)


class _SearchTool(Tool):
    def _invoke(self, params, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS)

    def get_parameterized_one_liner(self, params) -> str:
        return ""


class _SearchToolset(Toolset):
    def __init__(self, name, toolset_type, tool_specs, **kw):
        super().__init__(name=name, description=f"{name} toolset", type=toolset_type, **kw)
        self.tools = [_SearchTool(name=n, description=d) for n, d in tool_specs]


def _executor_with_search() -> ToolExecutor:
    core = _SearchToolset(
        "kubernetes/core", ToolsetType.BUILTIN, [("kubectl_get", "get k8s resources")]
    )
    aws = _SearchToolset(
        "aws_api", ToolsetType.MCP, [("call_aws", "run an aws cli command")]
    )
    kafka = _SearchToolset(
        "kafka", ToolsetType.MCP, [("list_clusters", "list MSK clusters")]
    )
    for ts in (core, aws, kafka):
        ts.status = ToolsetStatusEnum.ENABLED
    return ToolExecutor(toolsets=[core, aws, kafka])


def test_visible_tools_hide_mcp_and_include_load_tools():
    ex = _executor_with_search()
    names = {t["function"]["name"] for t in ex.get_visible_tools_openai_format(set())}
    assert "kubectl_get" in names  # built-in core stays loaded
    assert LOAD_TOOLS_NAME in names  # meta-tool exposed
    assert "call_aws" not in names  # MCP tools held back
    assert "list_clusters" not in names


def test_visible_tools_include_loaded_mcp_tool():
    ex = _executor_with_search()
    names = {
        t["function"]["name"]
        for t in ex.get_visible_tools_openai_format({"call_aws"})
    }
    assert "call_aws" in names  # loaded on demand → now visible
    assert "list_clusters" not in names  # other MCP tool still hidden


def test_search_deferred_tools_matches_mcp_only():
    ex = _executor_with_search()
    assert ex.search_deferred_tools("aws") == ["call_aws"]
    assert ex.search_deferred_tools("kafka") == ["list_clusters"]  # toolset-name match
    assert ex.search_deferred_tools("kubectl") == []  # core tool isn't deferrable
    assert ex.search_deferred_tools("no-such-thing") == []


def test_search_deferred_tools_falls_back_for_invalid_regex():
    # A malformed regex (e.g. the model emits "[" or "(") must not raise — it falls
    # back to a case-insensitive substring match (no match here → empty list).
    ex = _executor_with_search()
    assert ex.search_deferred_tools("[") == []
    assert ex.search_deferred_tools("(") == []
