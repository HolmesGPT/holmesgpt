"""Tests for the sub-agent delegation toolset (delegate_task tool).

Covers:
- delegate_task runs a child agentic loop and returns its final report
- recursion guard: the child loop does not get the delegate_task tool
- child LLM usage is attached to the tool result (llm_usage) and the parent
  agentic loop folds it into its cost stats
- llm_usage never leaks into the message sent back to the LLM
- toolset enablement is gated on HOLMES_ENABLE_SUBAGENTS
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.llm_usage import RequestStats
from holmes.core.models import ToolCallResult
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    Tool,
    ToolInvokeContext,
    Toolset,
    ToolsetStatusEnum,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.truncation.input_context_window_limiter import (
    ContextWindowLimiterOutput,
)
from holmes.plugins.toolsets.subagent.subagent_toolset import (
    DELEGATE_TASK_TOOL_NAME,
    SUBAGENT_PREAMBLE,
    DelegateTaskTool,
    SubAgentConfig,
    SubAgentToolset,
    build_subagent_system_prompt,
)

LIMIT_PATCH = "holmes.core.tool_calling_llm.compact_if_necessary"

DEFAULT_TOKEN_COUNT = ContextWindowUsage(
    total_tokens=100,
    system_tokens=0,
    tools_to_call_tokens=0,
    tools_tokens=0,
    user_tokens=0,
    assistant_tokens=0,
    other_tokens=0,
)


def _make_context_limiter_passthrough(messages, **_kwargs):
    return ContextWindowLimiterOutput(
        metadata={},
        messages=list(messages),
        events=[],
        max_context_size=128000,
        maximum_output_token=4096,
        tokens=DEFAULT_TOKEN_COUNT,
        conversation_history_compacted=False,
        compaction_usage=RequestStats(),
    )


def _make_llm_response(content="done", tool_calls=None, cost=0.001, prompt_tokens=50, completion_tokens=20):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = None
    msg.model_dump.return_value = {"role": "assistant", "content": content}
    resp.choices[0].message = msg
    resp.choices[0].finish_reason = "stop"
    resp._hidden_params = {"response_cost": cost}
    usage = MagicMock()
    usage.get = lambda key, default=0: {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "prompt_tokens_details": None,
        "completion_tokens_details": None,
    }.get(key, default)
    resp.usage = usage
    return resp


class EchoTool(Tool):
    name: str = "echo_tool"
    description: str = "Echo a value"

    def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        return StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS, data="echo", params=params
        )

    def get_parameterized_one_liner(self, params) -> str:
        return "echo"


@pytest.fixture
def mock_llm():
    llm = MagicMock(spec=LLM)
    llm.count_tokens.return_value = DEFAULT_TOKEN_COUNT
    llm.get_context_window_size.return_value = 128000
    llm.get_maximum_output_token.return_value = 4096
    llm.get_max_token_count_for_single_tool.return_value = 10000
    llm.model = "gpt-4o"
    return llm


@pytest.fixture
def real_executor():
    """Real ToolExecutor with an echo toolset and the subagent toolset enabled."""
    echo_ts = Toolset(
        name="echo",
        description="echo toolset",
        tools=[EchoTool()],
        enabled=True,
    )
    echo_ts.status = ToolsetStatusEnum.ENABLED

    sub_ts = SubAgentToolset()
    sub_ts.enabled = True
    sub_ts.status = ToolsetStatusEnum.ENABLED

    return ToolExecutor([echo_ts, sub_ts])


def _make_invoke_context(mock_llm, executor) -> ToolInvokeContext:
    return ToolInvokeContext(
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="tc_delegate_1",
        tool_name=DELEGATE_TASK_TOOL_NAME,
        tool_executor=executor,
    )


class TestDelegateTaskTool:
    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_returns_child_report_and_usage(self, _mock_limit, mock_llm, real_executor):
        mock_llm.completion.return_value = _make_llm_response(
            content="CHILD REPORT: pod crashed due to OOM",
            cost=0.01,
            prompt_tokens=100,
            completion_tokens=40,
        )

        tool = DelegateTaskTool()
        result = tool._invoke(
            params={"description": "check pod", "prompt": "Investigate pod foo in ns bar"},
            context=_make_invoke_context(mock_llm, real_executor),
        )

        assert result.status == StructuredToolResultStatus.SUCCESS
        assert result.data == "CHILD REPORT: pod crashed due to OOM"
        # Child usage propagated for parent-side accounting
        assert result.llm_usage is not None
        assert result.llm_usage["total_cost"] == pytest.approx(0.01)
        assert result.llm_usage["total_tokens"] == 140

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_child_does_not_get_delegate_tool(self, _mock_limit, mock_llm, real_executor):
        mock_llm.completion.return_value = _make_llm_response(content="report")

        tool = DelegateTaskTool()
        tool._invoke(
            params={"description": "t", "prompt": "do something"},
            context=_make_invoke_context(mock_llm, real_executor),
        )

        call_kwargs = mock_llm.completion.call_args.kwargs
        tool_names = [t["function"]["name"] for t in call_kwargs["tools"]]
        assert "echo_tool" in tool_names
        assert DELEGATE_TASK_TOOL_NAME not in tool_names

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_child_messages_structure(self, _mock_limit, mock_llm, real_executor):
        mock_llm.completion.return_value = _make_llm_response(content="report")

        tool = DelegateTaskTool()
        tool._invoke(
            params={"description": "t", "prompt": "Investigate pod foo in ns bar"},
            context=_make_invoke_context(mock_llm, real_executor),
        )

        msgs = mock_llm.completion.call_args.kwargs["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"].startswith(SUBAGENT_PREAMBLE)
        # TodoWrite procedural scaffolding must not be in the sub-agent prompt
        assert "TodoWrite" not in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        assert "Investigate pod foo in ns bar" in msgs[1]["content"]

    def test_empty_prompt_is_error(self, mock_llm, real_executor):
        tool = DelegateTaskTool()
        result = tool._invoke(
            params={"description": "t", "prompt": "  "},
            context=_make_invoke_context(mock_llm, real_executor),
        )
        assert result.status == StructuredToolResultStatus.ERROR

    def test_missing_executor_is_error(self, mock_llm):
        tool = DelegateTaskTool()
        result = tool._invoke(
            params={"description": "t", "prompt": "task"},
            context=ToolInvokeContext(
                llm=mock_llm,
                max_token_count=10000,
                tool_call_id="tc1",
                tool_name=DELEGATE_TASK_TOOL_NAME,
            ),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "unavailable" in (result.error or "")

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_child_exception_is_reported_as_error(self, _mock_limit, mock_llm, real_executor):
        mock_llm.completion.side_effect = RuntimeError("provider exploded")

        tool = DelegateTaskTool()
        result = tool._invoke(
            params={"description": "t", "prompt": "task"},
            context=_make_invoke_context(mock_llm, real_executor),
        )
        assert result.status == StructuredToolResultStatus.ERROR
        assert "provider exploded" in (result.error or "")


class TestUsagePropagation:
    """The parent agentic loop folds tool-level llm_usage into its stats."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_parent_stats_include_subagent_usage(self, _mock_limit, mock_llm):
        te = MagicMock(spec=ToolExecutor)
        te.get_all_tools_openai_format.return_value = []
        te.ensure_toolset_initialized.return_value = None
        mock_toolset = MagicMock()
        mock_toolset.name = "subagent"
        te.toolsets = [mock_toolset]
        te.enabled_toolsets = [mock_toolset]

        tc = MagicMock()
        tc.id = "tc_1"
        tc.function = MagicMock()
        tc.function.name = DELEGATE_TASK_TOOL_NAME
        tc.function.arguments = json.dumps({"description": "t", "prompt": "p"})

        resp_with_tool = _make_llm_response(
            content="delegating", tool_calls=[tc], cost=0.01, prompt_tokens=100, completion_tokens=50
        )
        resp_with_tool.choices[0].message.model_dump.return_value = {
            "role": "assistant",
            "content": "delegating",
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": DELEGATE_TASK_TOOL_NAME, "arguments": tc.function.arguments},
                }
            ],
        }
        resp_final = _make_llm_response(
            content="final", tool_calls=None, cost=0.02, prompt_tokens=200, completion_tokens=80
        )
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = ToolCallingLLM(
            tool_executor=te, max_steps=10, llm=mock_llm, tool_results_dir=None
        )
        subagent_result = ToolCallResult(
            tool_call_id="tc_1",
            tool_name=DELEGATE_TASK_TOOL_NAME,
            description="Sub-agent: t",
            result=StructuredToolResult(
                status=StructuredToolResultStatus.SUCCESS,
                data="child report",
                llm_usage={
                    "total_cost": 0.5,
                    "total_tokens": 1000,
                    "prompt_tokens": 700,
                    "completion_tokens": 300,
                },
            ),
        )
        ai._invoke_llm_tool_call = MagicMock(return_value=subagent_result)

        result = ai.call([{"role": "user", "content": "investigate everything"}])

        # parent: 0.01 + 0.02, child: 0.5
        assert result.total_cost == pytest.approx(0.53, abs=1e-9)
        assert result.total_tokens == 100 + 50 + 200 + 80 + 1000

    def test_llm_usage_not_serialized_to_llm_message(self):
        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data="report",
            llm_usage={"total_cost": 1.0},
        )
        tool_call_result = ToolCallResult(
            tool_call_id="tc1",
            tool_name=DELEGATE_TASK_TOOL_NAME,
            description="d",
            result=result,
        )
        message = tool_call_result.to_llm_message()
        assert "llm_usage" not in json.dumps(message)
        assert "llm_usage" not in result.model_dump()


class TestSubAgentToolsetEnablement:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HOLMES_ENABLE_SUBAGENTS", raising=False)
        assert SubAgentToolset().enabled is False

    def test_enabled_via_env_var(self, monkeypatch):
        monkeypatch.setenv("HOLMES_ENABLE_SUBAGENTS", "true")
        assert SubAgentToolset().enabled is True

    def test_has_llm_instructions(self):
        ts = SubAgentToolset()
        assert ts.llm_instructions
        assert "delegate_task" in ts.llm_instructions


class TestCloneWithoutTools:
    def test_clone_removes_tool(self, real_executor):
        clone = real_executor.clone_without_tools([DELEGATE_TASK_TOOL_NAME])
        assert clone.get_tool_by_name("echo_tool") is not None
        assert DELEGATE_TASK_TOOL_NAME not in clone.tools_by_name
        # original untouched
        assert DELEGATE_TASK_TOOL_NAME in real_executor.tools_by_name


class TestLeanChildPrompt:
    def test_skips_generic_scaffolding_keeps_toolset_instructions(self, real_executor):
        echo_ts = next(ts for ts in real_executor.enabled_toolsets if ts.name == "echo")
        echo_ts.llm_instructions = "Use echo_tool with the value parameter."
        prompt = build_subagent_system_prompt(real_executor)
        assert prompt.startswith(SUBAGENT_PREAMBLE)
        # generic_ask scaffolding must not be present
        assert "five whys" not in prompt
        assert "TodoWrite" not in prompt
        assert "MANDATORY" not in prompt
        # per-toolset operating instructions must be present
        assert "Use echo_tool with the value parameter." in prompt
        assert "## echo" in prompt


class TestChildModelSelection:
    def _tool(self, real_executor):
        sub_ts = next(ts for ts in real_executor.enabled_toolsets if ts.name == "subagent")
        return sub_ts.tools[0], sub_ts

    def test_defaults_to_parent_llm(self, mock_llm, real_executor, monkeypatch):
        monkeypatch.delenv("HOLMES_SUBAGENT_MODEL", raising=False)
        tool, _ = self._tool(real_executor)
        ctx = _make_invoke_context(mock_llm, real_executor)
        assert tool._get_child_llm(ctx) is mock_llm

    def test_env_var_model_inherits_parent_connection(self, mock_llm, real_executor, monkeypatch):
        monkeypatch.setenv("HOLMES_SUBAGENT_MODEL", "openai/anthropic/claude-haiku-4.5")
        mock_llm.api_key = "parent-key"
        mock_llm.api_base = "https://parent.example/api/v1"
        mock_llm.api_version = None
        mock_llm.tracer = None

        built = {}

        class FakeDefaultLLM:
            def __init__(self, model, api_key=None, api_base=None, api_version=None, tracer=None):
                built.update(model=model, api_key=api_key, api_base=api_base)
                self.model = model

        import holmes.core.llm as llm_mod
        monkeypatch.setattr(llm_mod, "DefaultLLM", FakeDefaultLLM)

        tool, sub_ts = self._tool(real_executor)
        ctx = _make_invoke_context(mock_llm, real_executor)
        child = tool._get_child_llm(ctx)
        assert child.model == "openai/anthropic/claude-haiku-4.5"
        assert built["api_key"] == "parent-key"
        assert built["api_base"] == "https://parent.example/api/v1"
        # cached: second call returns the same instance
        assert tool._get_child_llm(ctx) is child

    def test_toolset_config_takes_precedence_over_env(self, mock_llm, real_executor, monkeypatch):
        monkeypatch.setenv("HOLMES_SUBAGENT_MODEL", "env-model")

        class FakeDefaultLLM:
            def __init__(self, model, **kwargs):
                self.model = model

        import holmes.core.llm as llm_mod
        monkeypatch.setattr(llm_mod, "DefaultLLM", FakeDefaultLLM)

        tool, sub_ts = self._tool(real_executor)
        sub_ts.config = {"model": "config-model"}
        ctx = _make_invoke_context(mock_llm, real_executor)
        assert tool._get_child_llm(ctx).model == "config-model"
        sub_ts.config = None

    def test_same_model_as_parent_reuses_parent(self, mock_llm, real_executor, monkeypatch):
        monkeypatch.setenv("HOLMES_SUBAGENT_MODEL", "gpt-4o")
        mock_llm.model = "gpt-4o"
        tool, _ = self._tool(real_executor)
        ctx = _make_invoke_context(mock_llm, real_executor)
        assert tool._get_child_llm(ctx) is mock_llm

    def test_config_parsing(self):
        cfg = SubAgentConfig(**{"model": "m", "api_base": "b", "unknown_extra": 1})
        assert cfg.model == "m" and cfg.api_base == "b"
