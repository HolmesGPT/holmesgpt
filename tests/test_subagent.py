"""Tests for the dispatch_agent (subagent) flow.

These tests exercise the subagent system without spinning up a real LLM:
they mock LLM responses to verify that
  - the dispatch_agent tool is exposed only when subagents_enabled=True
  - invoking dispatch_agent spawns a child ToolCallingLLM that shares the
    parent's llm and tool_executor
  - the child cannot recursively dispatch further subagents
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.llm_usage import RequestStats
from holmes.core.subagent import (
    DISPATCH_AGENT_TOOL_NAME,
    DispatchAgentTool,
    DispatchAgentToolset,
)
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import (
    StructuredToolResult,
    StructuredToolResultStatus,
    ToolInvokeContext,
)
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.truncation.input_context_window_limiter import (
    ContextWindowLimiterOutput,
)


DEFAULT_TOKEN_COUNT = ContextWindowUsage(
    total_tokens=100,
    system_tokens=0,
    tools_to_call_tokens=0,
    tools_tokens=0,
    user_tokens=0,
    assistant_tokens=0,
    other_tokens=0,
)

LIMIT_PATCH = "holmes.core.tool_calling_llm.compact_if_necessary"


def _passthrough_limiter(messages, **_kwargs):
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


def _make_llm_response(content: str = "done", tool_calls: List[Any] | None = None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = None
    dump: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        dump["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    msg.model_dump.return_value = dump
    resp.choices[0].message = msg
    resp.to_json.return_value = json.dumps({"choices": [{"message": dump}]})
    resp._hidden_params = {"response_cost": 0.001}
    usage = MagicMock()
    usage.get = lambda key, default=0: {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }.get(key, default)
    resp.usage = usage
    return resp


@pytest.fixture
def mock_llm():
    llm = MagicMock(spec=LLM)
    llm.count_tokens.return_value = DEFAULT_TOKEN_COUNT
    llm.get_context_window_size.return_value = 128000
    llm.get_maximum_output_token.return_value = 4096
    llm.get_max_token_count_for_single_tool.return_value = 10000
    llm.model = "claude-sonnet-4-5"
    return llm


@pytest.fixture
def mock_tool_executor():
    te = MagicMock(spec=ToolExecutor)
    # No domain tools — only what the test injects via clone_with_extra_tools.
    te.get_all_tools_openai_format.return_value = []
    te.ensure_toolset_initialized.return_value = None
    te.oauth_connector = MagicMock()
    te.oauth_connector.get_toolset.return_value = None
    mock_toolset = MagicMock()
    mock_toolset.name = "core"
    te.toolsets = [mock_toolset]
    te.enabled_toolsets = [mock_toolset]
    te._tool_to_toolset = {}
    te.tools_by_name = {}

    # clone_with_extra_tools needs to behave like the real method: return a
    # new ToolExecutor-like object with the extra tool registered.
    def _clone_with_extra_tools(extra_tools):
        clone = MagicMock(spec=ToolExecutor)
        clone.get_all_tools_openai_format.return_value = [
            t.get_openai_format() for t in extra_tools
        ]
        clone.ensure_toolset_initialized.return_value = None
        clone.oauth_connector = te.oauth_connector
        clone.toolsets = list(te.toolsets)
        clone.enabled_toolsets = list(te.enabled_toolsets)
        clone._tool_to_toolset = dict(te._tool_to_toolset)
        clone.tools_by_name = {t.name: t for t in extra_tools}
        clone.get_toolset_name.return_value = None
        clone.get_tool_by_name = lambda name, user_id=None: clone.tools_by_name.get(name)
        clone.clone_with_extra_tools = _clone_with_extra_tools
        return clone

    # Wrap in MagicMock so tests can assert call/no-call on this attribute.
    te.clone_with_extra_tools = MagicMock(side_effect=_clone_with_extra_tools)

    # Subagent dispatch filters meta-tools via clone_without_tools; the mock
    # has no real tools to remove, so just hand back the same executor shape.
    def _clone_without_tools(excluded):
        clone = MagicMock(spec=ToolExecutor)
        clone.get_all_tools_openai_format.return_value = []
        clone.ensure_toolset_initialized.return_value = None
        clone.oauth_connector = te.oauth_connector
        clone.toolsets = list(te.toolsets)
        clone.enabled_toolsets = list(te.enabled_toolsets)
        clone._tool_to_toolset = dict(te._tool_to_toolset)
        clone.tools_by_name = {}
        clone.get_toolset_name.return_value = None
        clone.get_tool_by_name = lambda name, user_id=None: None
        clone.clone_with_extra_tools = _clone_with_extra_tools
        clone.clone_without_tools = _clone_without_tools
        return clone

    te.clone_without_tools = MagicMock(side_effect=_clone_without_tools)
    return te


def test_dispatch_agent_toolset_metadata():
    ts = DispatchAgentToolset()
    assert ts.name == "subagent"
    assert len(ts.tools) == 1
    tool = ts.tools[0]
    assert tool.name == DISPATCH_AGENT_TOOL_NAME
    assert "task_description" in tool.parameters
    assert "prompt" in tool.parameters


def test_subagents_disabled_does_not_register_tool(mock_llm, mock_tool_executor):
    ai = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=5,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=False,
    )
    # When disabled, the executor must not have been cloned to add the tool.
    mock_tool_executor.clone_with_extra_tools.assert_not_called()
    tool_names = [t["function"]["name"] for t in ai._get_tools()]
    assert DISPATCH_AGENT_TOOL_NAME not in tool_names
    assert ai.subagents_enabled is False


def test_subagents_enabled_registers_dispatch_tool(mock_llm, mock_tool_executor):
    ai = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=5,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=True,
    )
    assert ai.subagents_enabled is True
    tool_names = [t["function"]["name"] for t in ai._get_tools()]
    assert DISPATCH_AGENT_TOOL_NAME in tool_names


def test_dispatch_tool_rejects_empty_prompt(mock_llm, mock_tool_executor):
    parent = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=5,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=True,
    )
    tool = DispatchAgentTool()
    ctx = ToolInvokeContext(
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="tc_1",
        tool_name=DISPATCH_AGENT_TOOL_NAME,
        parent_agent=parent,
    )
    result = tool._invoke({"task_description": "x", "prompt": "   "}, ctx)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "non-empty 'prompt'" in (result.error or "")


def test_dispatch_tool_rejects_missing_parent(mock_llm):
    tool = DispatchAgentTool()
    ctx = ToolInvokeContext(
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="tc_1",
        tool_name=DISPATCH_AGENT_TOOL_NAME,
        parent_agent=None,
    )
    result = tool._invoke({"task_description": "x", "prompt": "do a thing"}, ctx)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "without a parent" in (result.error or "")


def test_dispatch_tool_refuses_recursion_from_subagent(mock_llm, mock_tool_executor):
    """If dispatch_agent ever leaks into a child's tool list, the runtime guard
    must refuse — the child's subagents_enabled is False."""
    child_like = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=5,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=False,
    )
    tool = DispatchAgentTool()
    ctx = ToolInvokeContext(
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="tc_1",
        tool_name=DISPATCH_AGENT_TOOL_NAME,
        parent_agent=child_like,
    )
    result = tool._invoke({"task_description": "x", "prompt": "do a thing"}, ctx)
    assert result.status == StructuredToolResultStatus.ERROR
    assert "subagent" in (result.error or "").lower()


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_dispatch_nests_child_span_under_parent(
    _mock_limit, mock_llm, mock_tool_executor
):
    """The dispatch tool should open a sub-span on the parent's trace_span and
    pass it into child.call() — that's what makes Braintrust render nested
    traces (parent → tool span → child → child tool spans)."""
    parent = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=10,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=True,
    )
    mock_llm.completion.side_effect = [
        _make_llm_response(content="found 7 restarts"),
    ]

    # A span double that records direct children so we can assert on the
    # nesting structure. Each probe tracks its own children — that lets us
    # distinguish the dispatch sub-span (direct child of parent_span) from
    # the gen_ai.chat span (grandchild, opened by child.call internally).
    class _SpanProbe:
        def __init__(self, name="root"):
            self.name = name
            self.logs: list = []
            self.children: list = []

        def start_span(self, name, type=None):
            child = _SpanProbe(name=name)
            self.children.append(child)
            return child

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def log(self, **kwargs):
            self.logs.append(kwargs)

        def set_attributes(self, **kwargs):
            pass

    parent_span = _SpanProbe(name="holmesgpt.tool.dispatch_agent")

    tool = DispatchAgentTool()
    ctx = ToolInvokeContext(
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="tc_1",
        tool_name=DISPATCH_AGENT_TOOL_NAME,
        parent_agent=parent,
        trace_span=parent_span,
    )
    result = tool._invoke(
        {"task_description": "check restarts", "prompt": "How many restarts?"},
        ctx,
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    # The parent span has exactly one direct child — the dispatch sub-span.
    # Anything else (e.g. the child's gen_ai.chat) lives one level deeper.
    assert len(parent_span.children) == 1
    child_span = parent_span.children[0]
    assert child_span.name.startswith("holmesgpt.subagent.")
    # The dispatch sub-span got both an input log and an output log.
    log_kinds = {tuple(sorted(call.keys())) for call in child_span.logs}
    assert any("input" in keys for keys in log_kinds)
    assert any("output" in keys for keys in log_kinds)
    # And the child agent's gen_ai.chat span is nested under the dispatch
    # sub-span (proves the trace_span really got plumbed into child.call).
    nested_names = [c.name for c in child_span.children]
    assert any("gen_ai.chat" in n for n in nested_names)


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_dispatch_attaches_child_stats_and_turns(
    _mock_limit, mock_llm, mock_tool_executor
):
    """The dispatch tool must attach the child's RequestStats and turn count
    onto the StructuredToolResult so the parent can roll them up."""
    parent = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=10,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=True,
    )
    # Make the child run two iterations: tool call, then final answer.
    # Use a real-looking response with usage so total_tokens > 0.
    final = _make_llm_response(content="found it: 7 restarts")
    mock_llm.completion.side_effect = [final]

    tool = DispatchAgentTool()
    ctx = ToolInvokeContext(
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="tc_1",
        tool_name=DISPATCH_AGENT_TOOL_NAME,
        parent_agent=parent,
    )
    result = tool._invoke(
        {"task_description": "x", "prompt": "do it"}, ctx
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    # Child stats should be attached to the tool result for parent rollup.
    assert result.subagent_stats is not None
    assert result.subagent_stats["total_tokens"] > 0
    # The child made exactly one LLM call (final answer with no tools).
    assert result.subagent_num_llm_calls == 1


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_dispatch_spawns_child_with_same_llm_and_executor(
    _mock_limit, mock_llm, mock_tool_executor
):
    """Calling dispatch_agent should spawn a child ToolCallingLLM that uses
    the parent's llm and tool_executor and returns its final answer back."""
    parent = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=10,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=True,
    )

    # When the child agent runs, the LLM should produce a single final answer.
    mock_llm.completion.side_effect = [
        _make_llm_response(content="The pod restarted 7 times."),
    ]

    tool = DispatchAgentTool()
    ctx = ToolInvokeContext(
        llm=mock_llm,
        max_token_count=10000,
        tool_call_id="tc_1",
        tool_name=DISPATCH_AGENT_TOOL_NAME,
        parent_agent=parent,
    )
    result = tool._invoke(
        {"task_description": "check restarts", "prompt": "How many restarts?"},
        ctx,
    )

    assert result.status == StructuredToolResultStatus.SUCCESS
    assert result.data == "The pod restarted 7 times."

    # The child should have called the parent's llm (same MagicMock instance).
    assert mock_llm.completion.called
    call_args = mock_llm.completion.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    # System prompt is the subagent system prompt + user prompt is the dispatch prompt
    assert messages[0]["role"] == "system"
    assert "sub-agent" in messages[0]["content"].lower() or "subagent" in messages[0]["content"].lower()
    assert messages[1]["role"] == "user"
    # Subagent wraps the parent's prompt with an output-spec reminder, so the
    # message content starts with the parent's text but may have an appended
    # format spec.
    assert messages[1]["content"].startswith("How many restarts?")


@patch(LIMIT_PATCH, side_effect=_passthrough_limiter)
def test_child_agent_cannot_recurse(_mock_limit, mock_llm, mock_tool_executor):
    """Children must be created with subagents_enabled=False so they cannot
    spawn further subagents (matches Claude Code: only top-level dispatches)."""
    from holmes.core.subagent import DispatchAgentTool as RealTool

    spawned_children: List[ToolCallingLLM] = []
    real_init = ToolCallingLLM.__init__

    def _tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        spawned_children.append(self)

    parent = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=10,
        llm=mock_llm,
        tool_results_dir=None,
        subagents_enabled=True,
    )
    # parent itself was tracked by clone-on-init; reset so we only count children.
    spawned_children.clear()

    mock_llm.completion.side_effect = [_make_llm_response(content="ok")]

    with patch.object(ToolCallingLLM, "__init__", _tracking_init):
        tool = RealTool()
        ctx = ToolInvokeContext(
            llm=mock_llm,
            max_token_count=10000,
            tool_call_id="tc_1",
            tool_name=DISPATCH_AGENT_TOOL_NAME,
            parent_agent=parent,
        )
        tool._invoke({"task_description": "x", "prompt": "do it"}, ctx)

    assert len(spawned_children) == 1
    child = spawned_children[0]
    assert child.subagents_enabled is False
    assert child.llm is mock_llm
    # Capability check (not just config check): the child's tool list as the
    # LLM sees it must NOT contain dispatch_agent, otherwise an LLM that
    # ignored the system prompt could still invoke it.
    child_tool_names = [t["function"]["name"] for t in child._get_tools()]
    assert DISPATCH_AGENT_TOOL_NAME not in child_tool_names
