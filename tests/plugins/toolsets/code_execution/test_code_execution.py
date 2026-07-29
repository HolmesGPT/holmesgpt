"""Unit + integration tests for the code_execution toolset ("code mode").

These run the REAL subprocess bridge (no mocking of the runner) against a set of
in-memory test tools, mirroring the pattern in test_bash_command_execution.py.
"""

from typing import List

import pytest

from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetStatusEnum,
    ToolsetTag,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.plugins.toolsets.code_execution.client_generator import (
    build_api_reference,
    eligible_tool_names,
)
from holmes.plugins.toolsets.code_execution.code_execution_toolset import (
    CodeExecutionToolset,
)
from tests.conftest import create_mock_tool_invoke_context

# All subprocess tests are stateful (temp dirs / sockets) but independent; group
# them so xdist keeps them on one worker and they don't fight for CPU.
pytestmark = pytest.mark.xdist_group("code_execution")


# ── test tools ──────────────────────────────────────────────────────────────


class EchoTool(Tool):
    name: str = "echo_tool"
    description: str = "Echo back the given text."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=params.get("text", "")
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "echo"


class ListNumbersTool(Tool):
    name: str = "list_numbers"
    description: str = "Return the list of integers 0..n-1 as JSON."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        n = int(params.get("n", 10))
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=list(range(n))
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "list_numbers"


class ErroringTool(Tool):
    name: str = "always_errors"
    description: str = "Always returns an error."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR, error="boom", data=None
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "always_errors"


class NeedsApprovalTool(Tool):
    name: str = "needs_approval_dynamic"
    description: str = "Returns APPROVAL_REQUIRED at invoke time."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.APPROVAL_REQUIRED, error="approve me"
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "needs_approval_dynamic"


class SecretTool(Tool):
    name: str = "secret_core_tool"
    description: str = "Should never be exposed to code mode."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data="x")

    def get_parameterized_one_liner(self, params) -> str:
        return "secret"


def _toolset(name, tools, *, is_core=False, approval_patterns=None) -> Toolset:
    ts = Toolset(
        name=name,
        description=f"{name} toolset",
        tools=tools,
        tags=[ToolsetTag.CORE],
        enabled=True,
        approval_required_tools=approval_patterns or [],
    )
    ts.status = ToolsetStatusEnum.ENABLED
    if is_core:
        ts._is_core = True
    return ts


@pytest.fixture
def wired_toolset():
    """A CodeExecutionToolset wired to a ToolExecutor with a mix of toolsets."""
    code_ts = CodeExecutionToolset()
    code_ts.prerequisites_callable({})
    code_ts.status = ToolsetStatusEnum.ENABLED

    data_ts = _toolset(
        "readonly_data",
        [ListNumbersTool(), EchoTool(), ErroringTool(), NeedsApprovalTool()],
    )
    core_ts = _toolset("core_stuff", [SecretTool()], is_core=True)
    bash_ts = _toolset("bash", [EchoTool.model_construct(name="bash")])
    approval_ts = _toolset(
        "approval_ts",
        [EchoTool.model_construct(name="gated_tool")],
        approval_patterns=["*"],
    )

    executor = ToolExecutor(toolsets=[code_ts, data_ts, core_ts, bash_ts, approval_ts])
    code_ts.set_tool_executor(executor)
    return code_ts


def _run(code_ts: CodeExecutionToolset, code: str, timeout: int = 30):
    tool = code_ts.tools[0]
    ctx = create_mock_tool_invoke_context(tool_name="run_python_code")
    return tool.invoke({"code": code, "timeout": timeout}, ctx)


# ── eligibility ───────────────────────────────────────────────────────────────


def test_eligibility_excludes_core_bash_and_approval(wired_toolset):
    names = eligible_tool_names(wired_toolset._tool_executor)
    assert "list_numbers" in names
    assert "echo_tool" in names
    assert "always_errors" in names
    assert "needs_approval_dynamic" in names  # eligible statically; guarded at dispatch
    # excluded surfaces:
    assert "secret_core_tool" not in names  # is_core
    assert "bash" not in names  # excluded toolset name
    assert "gated_tool" not in names  # approval_required_tools pattern
    assert "run_python_code" not in names  # no recursion


def test_api_reference_lists_functions(wired_toolset):
    ref = build_api_reference(wired_toolset._tool_executor)
    assert "holmes.list_numbers" in ref
    assert "holmes.echo_tool" in ref
    assert "secret_core_tool" not in ref


def test_llm_instructions_populated_after_wiring(wired_toolset):
    assert wired_toolset.llm_instructions
    assert "Code mode" in wired_toolset.llm_instructions
    assert "holmes.list_numbers" in wired_toolset.llm_instructions


# ── happy path + the core token-saving property ───────────────────────────────


def test_prints_only_filtered_result(wired_toolset):
    code = (
        "import json\n"
        "nums = json.loads(holmes.list_numbers(n=1000))\n"
        "evens = [x for x in nums if x % 2 == 0]\n"
        "print('num_evens', len(evens))\n"
    )
    result = _run(wired_toolset, code)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "num_evens 500" in result.data
    # The 1000-element raw list never appears in the returned data — only the
    # filtered summary does. This is the whole point of code mode.
    assert "999" not in result.data


def test_multiple_subcalls_collapse_into_one_result(wired_toolset):
    """N tool calls inside a script produce ONE tool result (call consolidation)."""
    code = (
        "a = holmes.echo_tool(text='one')\n"
        "b = holmes.echo_tool(text='two')\n"
        "c = holmes.echo_tool(text='three')\n"
        "print(a, b, c)\n"
    )
    result = _run(wired_toolset, code)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "one two three" in result.data
    assert "executed 3 tool call(s)" in result.data


def test_no_tool_calls_just_compute(wired_toolset):
    result = _run(wired_toolset, "print(2 + 2)")
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "4" in result.data


def test_no_output_is_no_data(wired_toolset):
    result = _run(wired_toolset, "x = 1 + 1")
    assert result.status == StructuredToolResultStatus.NO_DATA


# ── failure modes ─────────────────────────────────────────────────────────────


def test_syntax_error_returns_actionable_error(wired_toolset):
    result = _run(wired_toolset, "print('unterminated")
    assert result.status == StructuredToolResultStatus.ERROR
    assert result.return_code == 2
    assert "SyntaxError" in (result.data or "")


def test_runtime_exception_returns_traceback(wired_toolset):
    result = _run(wired_toolset, "raise ValueError('kaboom')")
    assert result.status == StructuredToolResultStatus.ERROR
    assert result.return_code == 1
    assert "kaboom" in (result.data or "")


def test_calling_unavailable_tool_is_attribute_error(wired_toolset):
    # bash is not exposed, so holmes.bash doesn't exist in the namespace.
    result = _run(wired_toolset, "holmes.bash(command='ls')")
    assert result.status == StructuredToolResultStatus.ERROR
    assert "AttributeError" in (result.data or "")


def test_subtool_error_raises_catchable_holmes_error(wired_toolset):
    code = (
        "try:\n"
        "    holmes.always_errors()\n"
        "    print('NO ERROR')\n"
        "except holmes.HolmesToolError as e:\n"
        "    print('caught:', e)\n"
    )
    result = _run(wired_toolset, code)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "caught:" in result.data
    assert "boom" in result.data


def test_approval_gated_subtool_is_denied_not_paused(wired_toolset):
    """An APPROVAL_REQUIRED result at dispatch becomes an error, never a pause."""
    code = (
        "try:\n"
        "    holmes.needs_approval_dynamic()\n"
        "    print('NO ERROR')\n"
        "except holmes.HolmesToolError as e:\n"
        "    print('denied:', e)\n"
    )
    result = _run(wired_toolset, code)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "denied:" in result.data
    assert "requires approval" in result.data


def test_timeout_kills_runaway_script(wired_toolset):
    result = _run(wired_toolset, "while True:\n    pass\n", timeout=1)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "timed out" in (result.error or "")


def test_missing_code_param(wired_toolset):
    tool = wired_toolset.tools[0]
    ctx = create_mock_tool_invoke_context(tool_name="run_python_code")
    result = tool.invoke({}, ctx)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "code" in (result.error or "")


def test_unwired_executor_errors_gracefully():
    code_ts = CodeExecutionToolset()
    code_ts.prerequisites_callable({})
    tool = code_ts.tools[0]
    ctx = create_mock_tool_invoke_context(tool_name="run_python_code")
    result = tool.invoke({"code": "print(1)"}, ctx)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "executor" in (result.error or "").lower()


# ── config / clamping ─────────────────────────────────────────────────────────


def test_timeout_is_clamped_to_max(wired_toolset):
    tool = wired_toolset.tools[0]
    assert tool._resolve_timeout({"timeout": 99999}) == 300  # max_timeout_seconds
    assert tool._resolve_timeout({"timeout": -5}) == 1  # negative floored to 1
    assert tool._resolve_timeout({"timeout": 0}) == 60  # 0 is falsy -> default
    assert tool._resolve_timeout({}) == 60  # default
    assert tool._resolve_timeout({"timeout": "not-an-int"}) == 60


def test_records_track_subcalls(wired_toolset):
    """The bridge records each sub-call (basis for later live SSE streaming)."""
    code = "holmes.echo_tool(text='hi'); holmes.always_errors()"
    result = _run(wired_toolset, code)
    # summary footer reflects both calls and their statuses
    assert "echo_tool" in result.data
    assert "always_errors" in result.data
