import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from holmes.config import Config, _toolset_tools_changed
from holmes.core.llm import LLM
from holmes.core.tools import (
    ApprovalRequirement, StructuredToolResult, StructuredToolResultStatus,
    Tool, ToolParameter, Toolset, ToolsetStatusEnum, ToolsetTag,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.decision.models import Candidate
from holmes.decision.tools import HolmesTools
from holmes.plugins.toolsets.mcp.toolset_mcp import RemoteMCPTool, RemoteMCPToolset


class ProbeTool(Tool):
    def get_parameterized_one_liner(self, params):
        return self.name

    def _invoke(self, params, context):
        return StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=params)


class ApprovalTool(ProbeTool):
    def requires_approval(self, params, context):
        return ApprovalRequirement(needs_approval=True, reason="needs operator approval")


def gateway_for(toolset):
    executor = ToolExecutor([toolset])
    config = Mock()
    config.create_tool_executor.return_value = executor
    config.cached_tool_executor = executor
    llm = Mock(spec=LLM)
    llm.get_max_token_count_for_single_tool.return_value = 8000
    return HolmesTools(config, llm, "test-user")


def make_query_toolset(required):
    tool = ProbeTool(name="query", description="query metrics", parameters={
        "limit": ToolParameter(type="integer", required=required)
    })
    return Toolset(name="source", description="source", tools=[tool], status=ToolsetStatusEnum.ENABLED)


def test_schema_only_mcp_changes_invalidate_cached_executor():
    old = make_query_toolset(False)
    new = make_query_toolset(True)
    assert _toolset_tools_changed([old], [new])
    assert not _toolset_tools_changed([old], [make_query_toolset(False)])

    config = Config()
    original = ToolExecutor([old])
    config._cached_tool_executor = original
    config._cached_executor_key = config._executor_cache_key([ToolsetTag.CORE], False)
    config._toolset_manager = Mock()
    config._toolset_manager.refresh_toolsets_and_get_changes.return_value = ([new], [])
    with patch("holmes.config.preload_oauth_tokens"), patch("holmes.config.eager_load_oauth_tools"):
        config.refresh_tool_executor()
    assert config.cached_tool_executor is not original
    assert config.cached_tool_executor.get_tool_by_name("query").parameters["limit"].required


def test_holmes_approval_and_session_allowlist_are_preserved():
    toolset = Toolset(name="source", description="source", status=ToolsetStatusEnum.ENABLED,
                      tools=[ApprovalTool(name="query", description="read")])
    gateway = gateway_for(toolset)
    candidate = Candidate(id="q", tool="query", arguments={}, purpose="inspect", direction="metrics")
    assert gateway.execute(candidate).status == "approval_required"
    assert not gateway.catalog()[0].retry_safe
    gateway.retry_safe_tools = {"query"}
    assert gateway.catalog()[0].retry_safe
    assert gateway.execute(candidate).status == "approval_required"
    gateway.allowed_tools = {"another_tool"}
    assert gateway.catalog() == []
    assert gateway.execute(candidate).status == "approval_required"


def test_real_mcp_stdio_discovery_and_invocation():
    server = Path(__file__).resolve().parents[2] / "examples/decision_agent/mcp_server.py"
    toolset = RemoteMCPToolset(
        name="observability", description="local fixture",
        config={"mode": "stdio", "command": sys.executable, "args": [str(server)]},
    )
    ok, error = toolset.prerequisites_callable(toolset.config)
    assert ok, error
    toolset.status = ToolsetStatusEnum.ENABLED
    gateway = gateway_for(toolset)
    names = {spec.name for spec in gateway.catalog()}
    assert names == {"service_logs", "service_metrics", "service_config", "service_instances"}
    result = gateway.execute(Candidate(
        id="metrics", tool="service_metrics", arguments={"service": "checkout", "minutes": 15},
        purpose="inspect pool pressure", direction="database",
    ))
    assert result.status == "success", result.error
    assert '"db_pool_pending":47' in result.data.replace(" ", "")
    assert '"simulated":true' in result.data.replace(" ", "").lower()

    # Preserve typed transport metadata across RemoteMCPTool's exception handler.
    with patch.object(RemoteMCPTool, "_invoke_async", new=AsyncMock(side_effect=TimeoutError("transport timed out"))):
        timed_out = gateway.execute(Candidate(
            id="retry-metrics", tool="service_metrics", arguments={"service": "checkout", "minutes": 15},
            purpose="inspect pool pressure", direction="database",
        ))
    assert timed_out.status == "error"
    assert timed_out.error_kind == "timeout"
