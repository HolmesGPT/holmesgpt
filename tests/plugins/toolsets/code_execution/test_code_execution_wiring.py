"""ToolCallingLLM must wire the ToolExecutor into code mode so it can dispatch
sibling tools. This is the integration seam between the agentic loop and the
code_execution toolset."""

from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import ToolInvokeContext, ToolsetStatusEnum, ToolsetTag, Toolset
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.code_execution.code_execution_toolset import (
    CodeExecutionToolset,
)
from tests.conftest import MockLLM
from tests.mocks.toolset_mocks import DummyTool


def _enabled(ts: Toolset) -> Toolset:
    ts.status = ToolsetStatusEnum.ENABLED
    return ts


def test_tool_calling_llm_wires_executor_into_code_mode():
    code_ts = CodeExecutionToolset()
    code_ts.prerequisites_callable({})
    _enabled(code_ts)

    data_ts = _enabled(
        Toolset(
            name="data",
            description="data",
            tools=[DummyTool()],
            tags=[ToolsetTag.CORE],
            enabled=True,
        )
    )

    executor = ToolExecutor(toolsets=[code_ts, data_ts])
    assert code_ts._tool_executor is None  # not wired yet

    ToolCallingLLM(
        tool_executor=executor,
        max_steps=1,
        llm=MockLLM(),
        tool_results_dir=None,
    )

    # The loop's __init__ wired it, and the API reference now reflects siblings.
    assert code_ts._tool_executor is executor
    assert "dummy_tool" in (code_ts.llm_instructions or "")


def test_wiring_hook_is_generic_and_ignores_plain_toolsets():
    """Toolsets without set_tool_executor are simply skipped (no crash)."""
    plain = _enabled(
        Toolset(
            name="plain",
            description="plain",
            tools=[DummyTool()],
            tags=[ToolsetTag.CORE],
            enabled=True,
        )
    )
    executor = ToolExecutor(toolsets=[plain])
    # Should not raise.
    ToolCallingLLM(
        tool_executor=executor,
        max_steps=1,
        llm=MockLLM(),
        tool_results_dir=None,
    )


def test_tool_executor_excluded_from_context_model_dump():
    """The request-scoped executor must not leak into serialized context."""
    executor = ToolExecutor(toolsets=[])
    ctx = ToolInvokeContext(
        llm=MockLLM(),
        max_token_count=1000,
        tool_call_id="x",
        tool_name="t",
        tool_executor=executor,
    )
    assert ctx.tool_executor is executor  # available at runtime
    assert "tool_executor" not in ctx.model_dump()  # but never serialized
