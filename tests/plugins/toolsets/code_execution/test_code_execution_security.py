"""Security boundary tests for code mode.

The generated ``holmes.*`` client only exposes eligible tools, but a script is
arbitrary Python and is handed the bridge socket path in ``HOLMES_CODE_SOCKET``.
So the real trust boundary is NOT the client stubs — it is the parent-side
allow-list check in ``dispatch`` (``code_execution_toolset._make_dispatch``).
These tests exercise that boundary directly, including a script that bypasses
the client and talks to the socket by hand.
"""

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
from holmes.plugins.toolsets.code_execution.code_execution_toolset import (
    CodeExecutionToolset,
)
from tests.conftest import create_mock_tool_invoke_context

pytestmark = pytest.mark.xdist_group("code_execution")

# If an excluded tool were ever dispatched, its result would contain this
# sentinel. Asserting the sentinel never appears proves the tool never ran.
_SENTINEL = "SECRET-DATA-LEAK-b3a1f7"


class EchoTool(Tool):
    name: str = "echo_tool"
    description: str = "Echo text back."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=params.get("text", "")
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "echo"


class SecretCoreTool(Tool):
    """Present in the executor but NOT eligible (its toolset is is_core).
    If dispatch ever ran it, the sentinel would leak into the output."""

    name: str = "secret_core_tool"
    description: str = "Must never be reachable from code mode."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data=_SENTINEL
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "secret"


class GatedTool(Tool):
    name: str = "gated_tool"
    description: str = "Approval-gated; must be denied inside a script."

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.APPROVAL_REQUIRED, error="approve me"
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "gated"


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
def wired():
    code_ts = CodeExecutionToolset()
    code_ts.prerequisites_callable({})
    code_ts.status = ToolsetStatusEnum.ENABLED

    data_ts = _toolset("readonly_data", [EchoTool()])
    core_ts = _toolset("core_stuff", [SecretCoreTool()], is_core=True)
    bash_ts = _toolset("bash", [EchoTool.model_construct(name="bash")])
    approval_ts = _toolset(
        "approval_ts", [GatedTool()], approval_patterns=["*"]
    )

    executor = ToolExecutor(
        toolsets=[code_ts, data_ts, core_ts, bash_ts, approval_ts]
    )
    code_ts.set_tool_executor(executor)
    return code_ts, executor


def _run(code_ts, code):
    ctx = create_mock_tool_invoke_context(tool_name="run_python_code")
    return code_ts.tools[0].invoke({"code": code, "timeout": 30}, ctx)


# ── the boundary itself: dispatch denies non-eligible names ───────────────────


@pytest.mark.parametrize(
    "name", ["secret_core_tool", "bash", "gated_tool", "does_not_exist"]
)
def test_bridge_denies_every_non_eligible_name(wired, name):
    """For each excluded/unknown name, a raw bridge request is denied WITHOUT
    the tool running — exercised through the real socket boundary (the client
    stubs are bypassed)."""
    code_ts, _ = wired
    code = (
        "import os, socket, json\n"
        "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "s.connect(os.environ['HOLMES_CODE_SOCKET'])\n"
        f"s.sendall((json.dumps({{'tool': {name!r}, 'params': {{}}}}) + chr(10)).encode())\n"
        "print(s.recv(65536).decode())\n"
    )
    result = _run(code_ts, code)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "not available" in (result.data or "")
    assert _SENTINEL not in (result.data or "")  # the tool never ran


def test_bridge_allows_eligible_name(wired):
    """Positive control: an eligible tool IS reachable over the same boundary."""
    code_ts, _ = wired
    result = _run(code_ts, "print(holmes.echo_tool(text='hi'))")
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "hi" in (result.data or "")


# ── the realistic attack: a script bypasses the client via the raw socket ─────


def test_raw_socket_bypass_of_client_is_denied(wired):
    """A script is arbitrary Python and is given HOLMES_CODE_SOCKET. Even talking
    to the bridge by hand (bypassing the generated holmes.* client), it cannot
    reach an excluded tool — the parent allow-list denies it."""
    code_ts, _ = wired
    code = (
        "import os, socket, json\n"
        "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "s.connect(os.environ['HOLMES_CODE_SOCKET'])\n"
        "s.sendall((json.dumps({'tool': 'secret_core_tool', 'params': {}}) + chr(10)).encode())\n"
        "print('BRIDGE_REPLY:', s.recv(65536).decode())\n"
    )
    result = _run(code_ts, code)
    assert result.status == StructuredToolResultStatus.SUCCESS  # script itself ran
    # ...but the excluded tool was denied and its data never came back:
    assert _SENTINEL not in (result.data or "")
    assert "not available" in (result.data or "")


def test_raw_socket_bypass_cannot_reach_bash(wired):
    """Same bypass, targeting bash (a mutation/approval surface) — denied."""
    code_ts, _ = wired
    code = (
        "import os, socket, json\n"
        "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "s.connect(os.environ['HOLMES_CODE_SOCKET'])\n"
        "s.sendall((json.dumps({'tool': 'bash', 'params': {'command': 'id'}}) + chr(10)).encode())\n"
        "print(s.recv(65536).decode())\n"
    )
    result = _run(code_ts, code)
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "not available" in (result.data or "")


# ── documented residual risk: no filesystem isolation ─────────────────────────


def test_subprocess_can_still_read_local_files_documented_gap(wired, tmp_path):
    """v1 has NO filesystem sandbox. This test documents that known, accepted
    limitation (see the design doc Security section): env secrets are stripped,
    but on-disk files the process can read are still reachable. If a future
    change adds fs isolation, this test should be updated to assert denial."""
    code_ts, _ = wired
    secret_file = tmp_path / "on_disk_secret.txt"
    secret_file.write_text("sa-token-xyz")
    code = f"print(open({str(secret_file)!r}).read())\n"
    result = _run(code_ts, code)
    # Documents current behavior: the file IS readable (no fs sandbox in v1).
    assert result.status == StructuredToolResultStatus.SUCCESS
    assert "sa-token-xyz" in (result.data or "")
