"""
Tests for ToolCallingLLM.call() and call_stream() loop mechanics.

Covers: multi-iteration tool calling, approval flows, cancellation,
cost accumulation, max_steps enforcement, response_format passthrough,
parallel tool execution, compaction cost tracking, and message structure.

Mocking strategy:
- Patch `compact_if_necessary` to avoid its internal LLM/token counting
- Mock `self.llm.completion` to control LLM responses
- Mock `self._invoke_llm_tool_call` to control tool execution
- Mock `self.llm.count_tokens` for token counting calls that happen outside
  of compact_if_necessary (e.g., after tool results, at final response)
"""

import json
import threading
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.models import PendingToolApproval, ToolApprovalDecision, ToolCallResult
from holmes.core.llm_usage import RequestStats
from holmes.core.tool_calling_llm import LLMInterruptedError, ToolCallingLLM
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.core.truncation.input_context_window_limiter import (
    ContextWindowLimiterOutput,
)
from holmes.utils.stream import StreamEvents, StreamMessage

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------

SIMPLE_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "kubectl_get",
        "description": "Get Kubernetes resources",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
        },
    },
}

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
    """Returns a ContextWindowLimiterOutput that passes messages through unchanged."""
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


def _make_mock_tool_call(tool_call_id="tc_1", tool_name="kubectl_get", arguments=None):
    tc = MagicMock()
    tc.id = tool_call_id
    tc.function = MagicMock()
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments or {"command": "kubectl get pods"})
    return tc


def _make_llm_response(content="done", tool_calls=None, cost=0.001, prompt_tokens=50, completion_tokens=20):
    """Create a mock LLM response matching litellm ModelResponse shape."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = None

    # model_dump must return a dict matching what gets appended to messages
    dump = {"role": "assistant", "content": content}
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

    # Cost/usage info
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


def _make_tool_call_result(tool_call_id="tc_1", tool_name="kubectl_get", data="pod1 Running"):
    return ToolCallResult(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        description=f"Ran {tool_name}",
        result=StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=data,
            params={"command": "kubectl get pods"},
        ),
    )


def _make_tool_call_result_error(tool_call_id="tc_1", tool_name="kubectl_get", error="command not found"):
    return ToolCallResult(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        description=f"Ran {tool_name}",
        result=StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=error,
            params={"command": "kubectl get pods"},
        ),
    )


def _make_tool_call_result_approval(tool_call_id="tc_1", tool_name="kubectl_delete",
                                     invocation="kubectl delete pod foo"):
    return ToolCallResult(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        description=f"Run {tool_name}",
        result=StructuredToolResult(
            status=StructuredToolResultStatus.APPROVAL_REQUIRED,
            invocation=invocation,
            params={"command": "kubectl delete pod foo", "suggested_prefixes": ["kubectl delete"]},
        ),
    )


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
def mock_tool_executor():
    te = MagicMock(spec=ToolExecutor)
    te.get_all_tools_openai_format.return_value = [SIMPLE_TOOL_OPENAI]
    te.ensure_toolset_initialized.return_value = None
    mock_toolset = MagicMock()
    mock_toolset.name = "kubectl"
    te.toolsets = [mock_toolset]
    te.enabled_toolsets = [mock_toolset]
    return te


@pytest.fixture
def make_ai(mock_llm, mock_tool_executor):
    """Factory that returns a ToolCallingLLM with default mocks."""
    def _make(max_steps=10):
        ai = ToolCallingLLM(
            tool_executor=mock_tool_executor,
            max_steps=max_steps,
            llm=mock_llm,
            tool_results_dir=None,
        )
        return ai
    return _make


LIMIT_PATCH = "holmes.core.tool_calling_llm.compact_if_necessary"


def _collect_stream_events(stream) -> List[StreamMessage]:
    return list(stream)


def _events_of_type(events: List[StreamMessage], event_type: StreamEvents) -> List[StreamMessage]:
    return [e for e in events if e.event == event_type]


# ---------------------------------------------------------------------------
# Test 1: Multi-iteration happy path
# ---------------------------------------------------------------------------


class TestMultiIterationHappyPath:
    """Mock LLM returns a tool call on first response, then a text answer on second."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_happy_path(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()
        resp_with_tool = _make_llm_response(content="Let me check", tool_calls=[tc])
        resp_final = _make_llm_response(content="All pods are running", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = make_ai()
        tool_result = _make_tool_call_result()
        ai._invoke_llm_tool_call = MagicMock(return_value=tool_result)

        messages = [{"role": "user", "content": "What pods are running?"}]
        result = ai.call(messages)

        # Verify result fields
        assert result.result == "All pods are running"
        assert result.num_llm_calls == 2
        assert len(result.tool_calls) == 1
        # LLMResult.tool_calls contains ToolCallResult objects (Pydantic coerces
        # the dicts from to_client_dict() back into ToolCallResult)
        assert result.tool_calls[0].tool_name == "kubectl_get"

        # Messages should contain: original + assistant(tool_calls) + tool + assistant(answer)
        assert result.messages is not None
        assert len(result.messages) >= 4

        # Cost fields populated
        assert result.prompt_tokens > 0
        assert result.completion_tokens > 0
        assert result.total_cost > 0

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_stream_happy_path(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()
        resp_with_tool = _make_llm_response(content="Let me check", tool_calls=[tc])
        resp_final = _make_llm_response(content="All pods are running", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = make_ai()
        tool_result = _make_tool_call_result()
        ai._invoke_llm_tool_call = MagicMock(return_value=tool_result)

        messages = [{"role": "user", "content": "What pods are running?"}]
        events = _collect_stream_events(ai.call_stream(msgs=messages))

        # Should have START_TOOL, TOOL_RESULT, TOKEN_COUNT, AI_MESSAGE, ANSWER_END
        start_tools = _events_of_type(events, StreamEvents.START_TOOL)
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        token_counts = _events_of_type(events, StreamEvents.TOKEN_COUNT)

        assert len(start_tools) == 1
        assert start_tools[0].data["tool_name"] == "kubectl_get"
        assert len(tool_results) == 1
        assert len(answer_ends) == 1
        assert answer_ends[0].data["content"] == "All pods are running"
        assert "messages" in answer_ends[0].data
        assert "metadata" in answer_ends[0].data
        assert len(token_counts) >= 1  # at least one TOKEN_COUNT event


# ---------------------------------------------------------------------------
# Test 3: Approval callback flow (call() → call_stream() → _prompt_for_approval_decisions)
# ---------------------------------------------------------------------------


class TestApprovalCallbackFlow:
    """Mock tool returns APPROVAL_REQUIRED, callback approves via _prompt_for_approval_decisions,
    then call() re-invokes call_stream() with tool_decisions and _execute_tool_decisions
    re-executes the tool."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_approval_approved(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call(tool_call_id="tc_del", tool_name="kubectl_delete")
        resp_with_tool = _make_llm_response(content="Deleting pod", tool_calls=[tc])
        resp_final = _make_llm_response(content="Pod deleted", tool_calls=None)
        # Round 1: LLM requests tool → APPROVAL_REQUIRED → stream stops
        # Round 2: _execute_tool_decisions re-executes tool, then LLM gives final answer
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        callback = MagicMock(return_value=(True, None))
        ai = make_ai()

        # First invocation of _invoke_llm_tool_call returns APPROVAL_REQUIRED
        approval_result = _make_tool_call_result_approval(
            tool_call_id="tc_del", tool_name="kubectl_delete"
        )
        # After approval, _execute_tool_decisions calls _invoke_llm_tool_call with user_approved=True
        approved_result = _make_tool_call_result(
            tool_call_id="tc_del", tool_name="kubectl_delete", data="pod deleted"
        )
        ai._invoke_llm_tool_call = MagicMock(
            side_effect=[approval_result, approved_result]
        )
        ai._is_tool_call_already_approved = MagicMock(return_value=False)

        messages = [{"role": "user", "content": "Delete the pod"}]
        result = ai.call(messages, approval_callback=callback)

        # Callback was invoked with a PendingToolApproval
        callback.assert_called_once()
        callback_arg = callback.call_args[0][0]
        assert isinstance(callback_arg, PendingToolApproval)
        assert callback_arg.tool_name == "kubectl_delete"

        # Tool was re-executed via _execute_tool_decisions (second _invoke_llm_tool_call call)
        assert ai._invoke_llm_tool_call.call_count == 2

        # Final result includes the approved tool, deduplicated
        assert result.result == "Pod deleted"
        tool_call_ids = [tc.tool_call_id for tc in result.tool_calls]
        assert len(tool_call_ids) == len(set(tool_call_ids)), "Duplicate tool_call_id in result"
        assert "tc_del" in tool_call_ids
        assert len(result.tool_calls) == 1

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_approval_denied_with_feedback(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call(tool_call_id="tc_del", tool_name="kubectl_delete")
        resp_with_tool = _make_llm_response(content="Deleting pod", tool_calls=[tc])
        resp_final = _make_llm_response(content="OK I won't delete it", tool_calls=None)
        # Round 1: LLM requests tool → APPROVAL_REQUIRED → stream stops
        # Round 2: _execute_tool_decisions adds denial error, LLM gives final answer
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        callback = MagicMock(return_value=(False, "try using namespace kube-system instead"))
        ai = make_ai()

        approval_result = _make_tool_call_result_approval(
            tool_call_id="tc_del", tool_name="kubectl_delete"
        )
        ai._invoke_llm_tool_call = MagicMock(return_value=approval_result)
        ai._is_tool_call_already_approved = MagicMock(return_value=False)

        messages = [{"role": "user", "content": "Delete the pod"}]
        result = ai.call(messages, approval_callback=callback)

        # Callback invoked
        callback.assert_called_once()

        # The tool result in messages should contain the feedback
        tool_messages = [m for m in result.messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        tool_content = tool_messages[0]["content"]
        assert "User feedback: try using namespace kube-system instead" in tool_content

        # Final answer from LLM
        assert result.result == "OK I won't delete it"


# ---------------------------------------------------------------------------
# Test 4: Cost accumulation across multiple iterations
# ---------------------------------------------------------------------------


class TestCostAccumulation:
    """3 iterations: 2 tool rounds + final answer. Verify costs sum correctly."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_costs_summed_across_iterations(self, _mock_limit, make_ai, mock_llm):
        tc1 = _make_mock_tool_call(tool_call_id="tc_1")
        tc2 = _make_mock_tool_call(tool_call_id="tc_2")

        resp1 = _make_llm_response(content="step 1", tool_calls=[tc1], cost=0.01, prompt_tokens=100, completion_tokens=50)
        resp2 = _make_llm_response(content="step 2", tool_calls=[tc2], cost=0.02, prompt_tokens=200, completion_tokens=80)
        resp3 = _make_llm_response(content="final answer", tool_calls=None, cost=0.03, prompt_tokens=300, completion_tokens=100)
        mock_llm.completion.side_effect = [resp1, resp2, resp3]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            side_effect=[
                _make_tool_call_result(tool_call_id="tc_1"),
                _make_tool_call_result(tool_call_id="tc_2"),
            ]
        )

        result = ai.call([{"role": "user", "content": "analyze"}])

        assert result.num_llm_calls == 3
        assert len(result.tool_calls) == 2

        # Costs should be summed
        assert result.total_cost == pytest.approx(0.06, abs=1e-9)
        assert result.prompt_tokens == 600  # 100 + 200 + 300
        assert result.completion_tokens == 230  # 50 + 80 + 100
        assert result.total_tokens == 830
        assert result.max_prompt_tokens_per_call == 300
        assert result.max_completion_tokens_per_call == 100


# ---------------------------------------------------------------------------
# Test 5: Cancellation via cancel_event
# ---------------------------------------------------------------------------


class TestCancellation:
    """cancel_event is set during tool execution, raises LLMInterruptedError."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_cancel_during_tool_execution(self, _mock_limit, make_ai, mock_llm):
        cancel_event = threading.Event()
        tc = _make_mock_tool_call()
        resp = _make_llm_response(content="running", tool_calls=[tc])
        mock_llm.completion.return_value = resp

        def tool_side_effect(*args, **kwargs):
            # Set cancel during tool execution
            cancel_event.set()
            return _make_tool_call_result()

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(side_effect=tool_side_effect)

        with pytest.raises(LLMInterruptedError):
            ai.call(
                [{"role": "user", "content": "check pods"}],
                cancel_event=cancel_event,
            )

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_cancel_before_llm_call(self, _mock_limit, make_ai, mock_llm):
        cancel_event = threading.Event()
        cancel_event.set()  # Already cancelled

        ai = make_ai()

        with pytest.raises(LLMInterruptedError):
            ai.call(
                [{"role": "user", "content": "check pods"}],
                cancel_event=cancel_event,
            )

        # LLM should never be called
        mock_llm.completion.assert_not_called()

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_cancel_after_llm_response(self, _mock_limit, make_ai, mock_llm):
        cancel_event = threading.Event()
        tc = _make_mock_tool_call()
        resp = _make_llm_response(content="running", tool_calls=[tc])

        def completion_side_effect(*args, **kwargs):
            cancel_event.set()  # Set cancel after LLM responds
            return resp

        mock_llm.completion.side_effect = completion_side_effect

        ai = make_ai()

        with pytest.raises(LLMInterruptedError):
            ai.call(
                [{"role": "user", "content": "check pods"}],
                cancel_event=cancel_event,
            )


# ---------------------------------------------------------------------------
# Test 7: Tool returning ERROR status
# ---------------------------------------------------------------------------


class TestToolError:
    """Tool returns ERROR, LLM receives error and continues to give final answer."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_continues_after_tool_error(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()
        resp_with_tool = _make_llm_response(content="checking", tool_calls=[tc])
        resp_final = _make_llm_response(content="The command failed, here is why...", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            return_value=_make_tool_call_result_error(error="permission denied")
        )

        result = ai.call([{"role": "user", "content": "check pods"}])

        assert result.result == "The command failed, here is why..."
        assert result.num_llm_calls == 2
        assert len(result.tool_calls) == 1

        # Verify the error tool result was included in messages for LLM
        tool_messages = [m for m in result.messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_stream_yields_error_tool_result(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()
        resp_with_tool = _make_llm_response(content="checking", tool_calls=[tc])
        resp_final = _make_llm_response(content="error occurred", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            return_value=_make_tool_call_result_error(error="permission denied")
        )

        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "check pods"}])
        )

        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].data["result"]["status"] == "error"

        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1
        assert answer_ends[0].data["content"] == "error occurred"


# ---------------------------------------------------------------------------
# Test 8: max_steps boundary
# ---------------------------------------------------------------------------


class TestMaxSteps:
    """max_steps=2, LLM always returns tool calls. Loop terminates."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_max_steps_forces_termination(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()
        # First response: tool call (iteration 1)
        resp1 = _make_llm_response(content="step 1", tool_calls=[tc])
        # Second response: tools=None forced by max_steps, so LLM must give text
        # But we'll mock it to return text since tools will be set to None
        resp2 = _make_llm_response(content="forced final answer", tool_calls=None)
        mock_llm.completion.side_effect = [resp1, resp2]

        ai = make_ai(max_steps=2)
        ai._invoke_llm_tool_call = MagicMock(return_value=_make_tool_call_result())

        result = ai.call([{"role": "user", "content": "check"}])

        assert result.result == "forced final answer"
        assert result.num_llm_calls == 2

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_max_steps_exceeded_raises(self, _mock_limit, make_ai, mock_llm):
        """If LLM keeps returning tool calls even on last iteration (shouldn't happen
        with tools=None, but test the safety net)."""
        tc = _make_mock_tool_call()
        # Both iterations return tool calls
        resp = _make_llm_response(content="still going", tool_calls=[tc])
        mock_llm.completion.return_value = resp

        ai = make_ai(max_steps=2)
        ai._invoke_llm_tool_call = MagicMock(return_value=_make_tool_call_result())

        with pytest.raises(Exception, match="Too many LLM calls"):
            ai.call([{"role": "user", "content": "check"}])


# ---------------------------------------------------------------------------
# Test 9: response_format passthrough
# ---------------------------------------------------------------------------


class TestResponseFormatPassthrough:
    """response_format is forwarded to litellm.completion."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_passes_response_format(self, _mock_limit, make_ai, mock_llm):
        resp = _make_llm_response(content='{"key": "value"}', tool_calls=None)
        mock_llm.completion.return_value = resp

        ai = make_ai()
        fmt = {"type": "json_object"}
        ai.call([{"role": "user", "content": "give me json"}], response_format=fmt)

        # Verify response_format was passed through
        call_kwargs = mock_llm.completion.call_args
        assert call_kwargs.kwargs.get("response_format") == fmt or call_kwargs[1].get("response_format") == fmt

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_stream_passes_response_format(self, _mock_limit, make_ai, mock_llm):
        resp = _make_llm_response(content='{"key": "value"}', tool_calls=None)
        mock_llm.completion.return_value = resp

        ai = make_ai()
        fmt = {"type": "json_object"}
        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "give me json"}], response_format=fmt)
        )

        call_kwargs = mock_llm.completion.call_args
        assert call_kwargs.kwargs.get("response_format") == fmt or call_kwargs[1].get("response_format") == fmt

        # Should still get ANSWER_END
        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1


# ---------------------------------------------------------------------------
# Test 2: No-tools path (LLM answers immediately)
# ---------------------------------------------------------------------------


class TestNoToolsPath:
    """LLM returns a text answer without requesting any tool calls."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_no_tools(self, _mock_limit, make_ai, mock_llm):
        resp = _make_llm_response(content="The answer is 42", tool_calls=None)
        mock_llm.completion.return_value = resp

        ai = make_ai()
        result = ai.call([{"role": "user", "content": "What is the answer?"}])

        assert result.result == "The answer is 42"
        assert result.num_llm_calls == 1
        assert result.tool_calls == []
        assert mock_llm.completion.call_count == 1

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_stream_no_tools(self, _mock_limit, make_ai, mock_llm):
        resp = _make_llm_response(content="The answer is 42", tool_calls=None)
        mock_llm.completion.return_value = resp

        ai = make_ai()
        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "What is the answer?"}])
        )

        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1
        assert answer_ends[0].data["content"] == "The answer is 42"

        # No tool events should be emitted
        start_tools = _events_of_type(events, StreamEvents.START_TOOL)
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(start_tools) == 0
        assert len(tool_results) == 0


# ---------------------------------------------------------------------------
# Test 6: Parallel tool execution (multiple tools in one LLM response)
# ---------------------------------------------------------------------------


class TestParallelToolExecution:
    """LLM requests multiple tools at once; all are executed and results returned."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_parallel_tools(self, _mock_limit, make_ai, mock_llm):
        tc1 = _make_mock_tool_call(tool_call_id="tc_a", tool_name="kubectl_get",
                                    arguments={"command": "kubectl get pods"})
        tc2 = _make_mock_tool_call(tool_call_id="tc_b", tool_name="kubectl_get",
                                    arguments={"command": "kubectl get services"})

        resp_with_tools = _make_llm_response(content="Checking both", tool_calls=[tc1, tc2])
        resp_final = _make_llm_response(content="Found 2 pods and 3 services", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tools, resp_final]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            side_effect=[
                _make_tool_call_result(tool_call_id="tc_a", data="pod1 Running\npod2 Running"),
                _make_tool_call_result(tool_call_id="tc_b", data="svc1\nsvc2\nsvc3"),
            ]
        )

        result = ai.call([{"role": "user", "content": "Show pods and services"}])

        assert result.result == "Found 2 pods and 3 services"
        assert result.num_llm_calls == 2
        assert len(result.tool_calls) == 2
        assert ai._invoke_llm_tool_call.call_count == 2

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_stream_parallel_tools(self, _mock_limit, make_ai, mock_llm):
        tc1 = _make_mock_tool_call(tool_call_id="tc_a")
        tc2 = _make_mock_tool_call(tool_call_id="tc_b")

        resp_with_tools = _make_llm_response(content="Checking", tool_calls=[tc1, tc2])
        resp_final = _make_llm_response(content="Done", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tools, resp_final]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            side_effect=[
                _make_tool_call_result(tool_call_id="tc_a"),
                _make_tool_call_result(tool_call_id="tc_b"),
            ]
        )

        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "Show all"}])
        )

        start_tools = _events_of_type(events, StreamEvents.START_TOOL)
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(start_tools) == 2
        assert len(tool_results) == 2


# ---------------------------------------------------------------------------
# Test 10: Stream approval flow with enable_tool_approval
# ---------------------------------------------------------------------------


class TestStreamApprovalFlow:
    """call_stream with enable_tool_approval emits APPROVAL_REQUIRED and stops."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_stream_approval_required_stops(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call(tool_call_id="tc_del", tool_name="kubectl_delete")
        resp = _make_llm_response(content="Deleting", tool_calls=[tc])
        mock_llm.completion.return_value = resp

        ai = make_ai()
        approval_result = _make_tool_call_result_approval(
            tool_call_id="tc_del", tool_name="kubectl_delete"
        )
        ai._invoke_llm_tool_call = MagicMock(return_value=approval_result)

        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Delete pod"}],
                enable_tool_approval=True,
            )
        )

        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 1
        assert len(approval_events[0].data["pending_approvals"]) == 1
        assert approval_events[0].data["pending_approvals"][0]["tool_call_id"] == "tc_del"

        # Stream should NOT have ANSWER_END since it stopped for approval
        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 0

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_stream_approval_disabled_converts_to_error(self, _mock_limit, make_ai, mock_llm):
        """When enable_tool_approval=False (default), APPROVAL_REQUIRED becomes ERROR."""
        tc = _make_mock_tool_call(tool_call_id="tc_del", tool_name="kubectl_delete")
        resp_with_tool = _make_llm_response(content="Deleting", tool_calls=[tc])
        resp_final = _make_llm_response(content="Could not delete", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = make_ai()
        approval_result = _make_tool_call_result_approval(
            tool_call_id="tc_del", tool_name="kubectl_delete"
        )
        ai._invoke_llm_tool_call = MagicMock(return_value=approval_result)

        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Delete pod"}],
                enable_tool_approval=False,
            )
        )

        # Should NOT have APPROVAL_REQUIRED event
        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 0

        # Should have error tool result and final answer
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].data["result"]["status"] == "error"

        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1


# ---------------------------------------------------------------------------
# Test 11: Compaction cost accumulation
# ---------------------------------------------------------------------------


class TestCompactionCosts:
    """Compaction tokens/cost from compact_if_necessary are accumulated."""

    def test_call_accumulates_compaction_costs(self, make_ai, mock_llm):
        compaction = RequestStats(
            total_tokens=500, prompt_tokens=400, completion_tokens=100, total_cost=0.005
        )
        limiter_output_with_compaction = ContextWindowLimiterOutput(
            metadata={},
            messages=[{"role": "user", "content": "analyze"}],
            events=[],
            max_context_size=128000,
            maximum_output_token=4096,
            tokens=DEFAULT_TOKEN_COUNT,
            conversation_history_compacted=True,
            compaction_usage=compaction,
        )
        limiter_output_normal = ContextWindowLimiterOutput(
            metadata={},
            messages=[{"role": "user", "content": "analyze"}],
            events=[],
            max_context_size=128000,
            maximum_output_token=4096,
            tokens=DEFAULT_TOKEN_COUNT,
            conversation_history_compacted=False,
            compaction_usage=RequestStats(),
        )

        tc = _make_mock_tool_call()
        resp1 = _make_llm_response(content="step", tool_calls=[tc], cost=0.01,
                                    prompt_tokens=100, completion_tokens=50)
        resp2 = _make_llm_response(content="done", tool_calls=None, cost=0.02,
                                    prompt_tokens=200, completion_tokens=80)
        mock_llm.completion.side_effect = [resp1, resp2]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(return_value=_make_tool_call_result())

        with patch(LIMIT_PATCH, side_effect=[limiter_output_with_compaction, limiter_output_normal]):
            result = ai.call([{"role": "user", "content": "analyze"}])

        # Costs = compaction(0.005) + LLM1(0.01) + LLM2(0.02) = 0.035
        assert result.total_cost == pytest.approx(0.035, abs=1e-9)
        # Tokens = compaction(500) + LLM1(150) + LLM2(280) = 930
        assert result.total_tokens == 930
        assert result.num_compactions == 1


# ---------------------------------------------------------------------------
# Test 12: call_stream includes costs in metadata
# ---------------------------------------------------------------------------


class TestStreamCostsInMetadata:
    """call_stream includes costs dict in TOKEN_COUNT events."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_stream_metadata_has_costs(self, _mock_limit, make_ai, mock_llm):
        resp = _make_llm_response(content="answer", tool_calls=None, cost=0.01,
                                   prompt_tokens=100, completion_tokens=50)
        mock_llm.completion.return_value = resp

        ai = make_ai()
        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "question"}])
        )

        token_counts = _events_of_type(events, StreamEvents.TOKEN_COUNT)
        assert len(token_counts) >= 1

        # The metadata in TOKEN_COUNT should include costs (nested under "metadata")
        tc_data = token_counts[0].data
        assert "metadata" in tc_data
        assert "costs" in tc_data["metadata"]
        assert tc_data["metadata"]["costs"]["total_cost"] == pytest.approx(0.01, abs=1e-6)


# ---------------------------------------------------------------------------
# Test 13: Message structure validation
# ---------------------------------------------------------------------------


class TestMessageStructure:
    """Verify the ordering and structure of messages in result."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_message_ordering(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()
        resp_with_tool = _make_llm_response(content="Let me check", tool_calls=[tc])
        resp_final = _make_llm_response(content="All good", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(return_value=_make_tool_call_result())

        messages = [{"role": "user", "content": "Check pods"}]
        result = ai.call(messages)

        # Message sequence: user -> assistant(tool_calls) -> tool -> assistant(text)
        roles = [m["role"] for m in result.messages]
        assert roles[0] == "user"
        assert roles[1] == "assistant"
        assert roles[2] == "tool"
        assert roles[3] == "assistant"

        # Assistant message with tool calls should have tool_calls key
        assert "tool_calls" in result.messages[1]

        # Tool message should have tool_call_id
        assert "tool_call_id" in result.messages[2]
        assert result.messages[2]["name"] == "kubectl_get"

        # Final assistant message should have content
        assert result.messages[3]["content"] == "All good"

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_stream_answer_end_contains_messages(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()
        resp_with_tool = _make_llm_response(content="Checking", tool_calls=[tc])
        resp_final = _make_llm_response(content="Done", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tool, resp_final]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(return_value=_make_tool_call_result())

        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "Check"}])
        )

        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1
        msgs = answer_ends[0].data["messages"]
        # Should have at least: user, assistant(tool), tool, assistant(final)
        assert len(msgs) >= 4
        roles = [m["role"] for m in msgs]
        assert "tool" in roles


# ---------------------------------------------------------------------------
# Test 14: call() with system + user messages
# ---------------------------------------------------------------------------


class TestCallWithPromptMessages:
    """call() works correctly with system + user message list."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_with_system_and_user(self, _mock_limit, make_ai, mock_llm):
        resp = _make_llm_response(content="response", tool_calls=None)
        mock_llm.completion.return_value = resp

        ai = make_ai()
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
        ]
        result = ai.call(messages)

        assert result.result == "response"
        # Verify messages passed to completion have system + user
        call_args = mock_llm.completion.call_args
        msgs = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"


# ---------------------------------------------------------------------------
# Test 15: Equivalence — call() result matches call_stream() ANSWER_END
# ---------------------------------------------------------------------------


class TestEquivalence:
    """call() result fields match what you'd reconstruct from call_stream() ANSWER_END."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_call_vs_stream_equivalence(self, _mock_limit, make_ai, mock_llm):
        tc = _make_mock_tool_call()

        def _make_responses():
            """Generate fresh response mocks (each can only be consumed once)."""
            resp_tool = _make_llm_response(content="checking", tool_calls=[tc], cost=0.01, prompt_tokens=100, completion_tokens=50)
            resp_final = _make_llm_response(content="All good", tool_calls=None, cost=0.02, prompt_tokens=200, completion_tokens=80)
            return [resp_tool, resp_final]

        tool_result = _make_tool_call_result()

        # Run call()
        mock_llm.completion.side_effect = _make_responses()
        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(return_value=tool_result)
        call_result = ai.call([{"role": "user", "content": "check"}])

        # Run call_stream()
        mock_llm.completion.side_effect = _make_responses()
        ai2 = make_ai()
        ai2._invoke_llm_tool_call = MagicMock(return_value=tool_result)
        events = _collect_stream_events(
            ai2.call_stream(msgs=[{"role": "user", "content": "check"}])
        )
        answer_end = _events_of_type(events, StreamEvents.ANSWER_END)[0].data

        # Compare result text
        assert call_result.result == answer_end["content"]

        # Compare message count and structure
        assert len(call_result.messages) == len(answer_end["messages"])

        # Compare tool_calls count
        assert len(call_result.tool_calls) == len(answer_end["tool_calls"])

        # Compare num_llm_calls
        assert call_result.num_llm_calls == answer_end["num_llm_calls"]

        # Compare costs
        stream_costs = answer_end["costs"]
        assert call_result.total_cost == pytest.approx(stream_costs["total_cost"], abs=1e-9)
        assert call_result.prompt_tokens == stream_costs["prompt_tokens"]
        assert call_result.completion_tokens == stream_costs["completion_tokens"]


# ---------------------------------------------------------------------------
# Test 16: Approval via re-invocation (post-refactor)
# ---------------------------------------------------------------------------


class TestApprovalViaReinvocation:
    """Full approval flow through call_stream() → APPROVAL_REQUIRED → re-invocation."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_stream_approval_reinvocation(self, _mock_limit, make_ai, mock_llm):
        """Manually re-invoke call_stream with tool_decisions after APPROVAL_REQUIRED."""
        tc = _make_mock_tool_call(tool_call_id="tc_del", tool_name="kubectl_delete")
        resp_with_tool = _make_llm_response(content="Deleting", tool_calls=[tc])
        resp_final = _make_llm_response(content="Pod deleted", tool_calls=None)

        ai = make_ai()

        # Round 1: get APPROVAL_REQUIRED
        mock_llm.completion.side_effect = [resp_with_tool]
        approval_result = _make_tool_call_result_approval(
            tool_call_id="tc_del", tool_name="kubectl_delete"
        )
        ai._invoke_llm_tool_call = MagicMock(return_value=approval_result)

        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Delete pod"}],
                enable_tool_approval=True,
            )
        )
        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 1
        saved_messages = approval_events[0].data["messages"]

        # Round 2: re-invoke with approval decision
        mock_llm.completion.side_effect = [resp_final]
        approved_tool_result = _make_tool_call_result(
            tool_call_id="tc_del", tool_name="kubectl_delete", data="pod deleted"
        )
        ai._invoke_llm_tool_call = MagicMock(return_value=approved_tool_result)

        decisions = [ToolApprovalDecision(tool_call_id="tc_del", approved=True)]

        events2 = _collect_stream_events(
            ai.call_stream(
                msgs=saved_messages,
                enable_tool_approval=True,
                tool_decisions=decisions,
            )
        )

        answer_ends = _events_of_type(events2, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1
        assert answer_ends[0].data["content"] == "Pod deleted"

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_mixed_batch_approval(self, _mock_limit, make_ai, mock_llm):
        """Two tools in one batch: one succeeds, one needs approval. Both appear in result."""
        tc_ok = _make_mock_tool_call(tool_call_id="tc_ok", tool_name="kubectl_get")
        tc_del = _make_mock_tool_call(tool_call_id="tc_del", tool_name="kubectl_delete")

        resp_with_tools = _make_llm_response(content="Running both", tool_calls=[tc_ok, tc_del])
        resp_final = _make_llm_response(content="Done", tool_calls=None)
        mock_llm.completion.side_effect = [resp_with_tools, resp_final]

        callback = MagicMock(return_value=(True, None))
        ai = make_ai()

        ok_result = _make_tool_call_result(tool_call_id="tc_ok", data="pods listed")
        approval_result = _make_tool_call_result_approval(
            tool_call_id="tc_del", tool_name="kubectl_delete"
        )
        approved_result = _make_tool_call_result(
            tool_call_id="tc_del", tool_name="kubectl_delete", data="pod deleted"
        )

        # First invocation: tc_ok succeeds, tc_del needs approval
        # Second invocation (after approval): tc_del re-executed via _execute_tool_decisions
        del_call_count = 0

        def _route_tool_call(tool_to_call, **kwargs):
            nonlocal del_call_count
            if tool_to_call.id == "tc_ok":
                return ok_result
            elif tool_to_call.id == "tc_del":
                del_call_count += 1
                return approval_result if del_call_count == 1 else approved_result
            raise ValueError(f"Unexpected tool call: {tool_to_call.id}")

        ai._invoke_llm_tool_call = MagicMock(side_effect=_route_tool_call)
        ai._is_tool_call_already_approved = MagicMock(return_value=False)

        result = ai.call([{"role": "user", "content": "Get pods and delete one"}], approval_callback=callback)

        assert result.result == "Done"
        # Both tools should appear exactly once (deduplicated)
        tool_call_ids = [tc.tool_call_id for tc in result.tool_calls]
        assert len(tool_call_ids) == len(set(tool_call_ids)), "Duplicate tool_call_id in result"
        assert set(tool_call_ids) == {"tc_ok", "tc_del"}
        tool_names = [tc.tool_name for tc in result.tool_calls]
        assert "kubectl_get" in tool_names
        assert "kubectl_delete" in tool_names


# ---------------------------------------------------------------------------
# Test: Blackbox SSE event shapes (public contract for HTTP clients)
# ---------------------------------------------------------------------------

EXPECTED_COSTS_KEYS = {
    "total_cost", "total_tokens", "prompt_tokens", "completion_tokens",
    "cached_tokens", "reasoning_tokens", "max_completion_tokens_per_call",
    "max_prompt_tokens_per_call", "num_compactions",
}

EXPECTED_TOKEN_COUNT_METADATA_KEYS = {"costs", "usage", "tokens", "max_tokens", "max_output_tokens"}

EXPECTED_ANSWER_END_KEYS = {
    "content", "messages", "metadata", "tool_calls", "num_llm_calls", "prompt", "costs",
}

EXPECTED_APPROVAL_REQUIRED_KEYS = {
    "content", "messages", "pending_approvals",
    "pending_frontend_tool_calls", "num_llm_calls", "costs",
}


class TestSSEEventShapes:
    """Assert the exact JSON key sets an HTTP client sees in each SSE event type.

    These tests treat call_stream() as a black box and only inspect the
    StreamMessage.data dicts — the same dicts that get json.dumps'd into SSE
    ``data:`` lines by stream_chat_formatter().
    """

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_token_count_event_shape(self, _mock_limit, make_ai, mock_llm):
        """TOKEN_COUNT events carry metadata with costs, usage, tokens, and limits."""
        tc = _make_mock_tool_call(tool_call_id="tc_shape")
        resp1 = _make_llm_response(content="working", tool_calls=[tc], cost=0.01)
        resp2 = _make_llm_response(content="done", tool_calls=None, cost=0.02)
        mock_llm.completion.side_effect = [resp1, resp2]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            return_value=_make_tool_call_result(tool_call_id="tc_shape")
        )

        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "check"}])
        )

        token_counts = _events_of_type(events, StreamEvents.TOKEN_COUNT)
        assert len(token_counts) >= 1

        for tc_event in token_counts:
            meta = tc_event.data["metadata"]
            assert set(meta.keys()) >= EXPECTED_TOKEN_COUNT_METADATA_KEYS, (
                f"TOKEN_COUNT metadata missing keys: "
                f"{EXPECTED_TOKEN_COUNT_METADATA_KEYS - set(meta.keys())}"
            )
            assert set(meta["costs"].keys()) == EXPECTED_COSTS_KEYS, (
                f"costs keys mismatch: got {set(meta['costs'].keys())}"
            )

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_answer_end_event_shape(self, _mock_limit, make_ai, mock_llm):
        """ANSWER_END event carries all required top-level keys."""
        tc = _make_mock_tool_call(tool_call_id="tc_ae")
        resp1 = _make_llm_response(content="step", tool_calls=[tc], cost=0.01)
        resp2 = _make_llm_response(content="answer", tool_calls=None, cost=0.02)
        mock_llm.completion.side_effect = [resp1, resp2]

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            return_value=_make_tool_call_result(tool_call_id="tc_ae")
        )

        events = _collect_stream_events(
            ai.call_stream(msgs=[{"role": "user", "content": "go"}])
        )

        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1
        data = answer_ends[0].data

        assert set(data.keys()) == EXPECTED_ANSWER_END_KEYS, (
            f"ANSWER_END keys mismatch: got {set(data.keys())}"
        )
        assert set(data["costs"].keys()) == EXPECTED_COSTS_KEYS
        assert isinstance(data["messages"], list)
        assert isinstance(data["tool_calls"], list)
        assert isinstance(data["num_llm_calls"], int)

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_approval_required_event_shape(self, _mock_limit, make_ai, mock_llm):
        """APPROVAL_REQUIRED event carries the expected top-level keys."""
        tc = _make_mock_tool_call(tool_call_id="tc_apr", tool_name="kubectl_delete")
        resp = _make_llm_response(content="deleting", tool_calls=[tc])
        mock_llm.completion.return_value = resp

        ai = make_ai()
        ai._invoke_llm_tool_call = MagicMock(
            return_value=_make_tool_call_result_approval(tool_call_id="tc_apr")
        )

        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "delete pod"}],
                enable_tool_approval=True,
            )
        )

        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 1
        data = approval_events[0].data

        assert set(data.keys()) == EXPECTED_APPROVAL_REQUIRED_KEYS, (
            f"APPROVAL_REQUIRED keys mismatch: got {set(data.keys())}"
        )
        assert set(data["costs"].keys()) == EXPECTED_COSTS_KEYS
        assert isinstance(data["pending_approvals"], list)
        assert isinstance(data["pending_frontend_tool_calls"], list)
        assert len(data["pending_approvals"]) > 0


# ---------------------------------------------------------------------------
# Test: Frontend tool flows (pause-mode via FrontendPauseTool in cloned executor)
# ---------------------------------------------------------------------------


def _make_ai_with_frontend_tools(make_ai_fn, mock_tool_executor, tool_names=None):
    """Create a ToolCallingLLM with FrontendPauseTool(s) injected into the executor.

    This mirrors what server.py does: clone the executor, inject frontend tools,
    create a new ToolCallingLLM with the cloned executor.
    """
    from holmes.core.tools_utils.frontend_tools import build_frontend_pause_tool
    from holmes.core.tools_utils.tool_executor import ToolExecutor

    if tool_names is None:
        tool_names = [("show_chart", "Display a chart to the user", {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string"},
                "data_source": {"type": "string"},
            },
        })]

    frontend_tools = [
        build_frontend_pause_tool(name=name, description=desc, parameters=params)
        for name, desc, params in tool_names
    ]

    # Build a real (not mocked) ToolExecutor clone with frontend tools
    clone = object.__new__(ToolExecutor)
    mock_toolset = MagicMock()
    mock_toolset.name = "kubectl"
    clone.toolsets = [mock_toolset]
    clone.enabled_toolsets = [mock_toolset]
    clone.tools_by_name = {}
    clone._tool_to_toolset = {}

    for ft in frontend_tools:
        clone.tools_by_name[ft.name] = ft

    # Include both backend + frontend tools in OpenAI format
    backend_tools = mock_tool_executor.get_all_tools_openai_format.return_value or []
    frontend_openai = [ft.get_openai_format() for ft in frontend_tools]
    clone.get_all_tools_openai_format = MagicMock(
        return_value=backend_tools + frontend_openai
    )
    clone.ensure_toolset_initialized = MagicMock(return_value=None)

    ai = make_ai_fn()
    ai.tool_executor = clone
    return ai


class TestFrontendToolPauseFlow:
    """Test pause-mode frontend tools: LLM calls a frontend tool, stream pauses
    with approval_required event, client resumes with frontend_tool_results.

    Frontend tools are injected as FrontendPauseTool instances into a cloned
    ToolExecutor, mirroring the server.py approach. call_stream has no
    frontend_tool_names or frontend_tool_definitions parameters.
    """

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_frontend_tool_pauses_stream(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """When LLM calls a frontend tool, stream emits approval_required with
        pending_frontend_tool_calls and stops (no ANSWER_END)."""
        ft_call = _make_mock_tool_call(
            tool_call_id="ft_1", tool_name="show_chart",
            arguments={"chart_type": "line", "data_source": "cpu_usage"},
        )
        resp = _make_llm_response(content="Let me show you a chart", tool_calls=[ft_call])
        mock_llm.completion.return_value = resp

        ai = _make_ai_with_frontend_tools(make_ai, mock_tool_executor)
        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Show CPU chart"}],
            )
        )

        # Should have START_TOOL for the frontend tool
        start_events = _events_of_type(events, StreamEvents.START_TOOL)
        assert len(start_events) == 1
        assert start_events[0].data["tool_name"] == "show_chart"

        # Should have APPROVAL_REQUIRED with pending_frontend_tool_calls
        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 1
        data = approval_events[0].data
        assert len(data["pending_frontend_tool_calls"]) == 1
        fc = data["pending_frontend_tool_calls"][0]
        assert fc["tool_call_id"] == "ft_1"
        assert fc["tool_name"] == "show_chart"
        assert fc["arguments"] == {"chart_type": "line", "data_source": "cpu_usage"}

        # Should NOT have ANSWER_END (stream paused)
        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 0

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_frontend_tool_mixed_with_backend(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """When LLM calls both backend and frontend tools in same iteration,
        backend tools execute and frontend tools cause a pause."""
        backend_call = _make_mock_tool_call(tool_call_id="bt_1", tool_name="kubectl_get")
        frontend_call = _make_mock_tool_call(
            tool_call_id="ft_1", tool_name="show_chart",
            arguments={"chart_type": "bar", "data_source": "memory"},
        )
        resp = _make_llm_response(
            content="checking", tool_calls=[backend_call, frontend_call],
        )
        mock_llm.completion.return_value = resp

        ai = _make_ai_with_frontend_tools(make_ai, mock_tool_executor)
        ai._invoke_llm_tool_call = MagicMock(
            side_effect=lambda tool_to_call, **kwargs: (
                _make_tool_call_result(tool_call_id="bt_1")
                if tool_to_call.function.name == "kubectl_get"
                else ai._invoke_llm_tool_call.default_return_value
            )
        )
        # For the show_chart tool, _invoke_llm_tool_call will go through the real
        # FrontendPauseTool.invoke() path via _directly_invoke_tool_call, so we
        # need to let it work. But since we mock _invoke_llm_tool_call, we need
        # to return the right thing for each tool.
        def _route_tool_call(tool_to_call, **kwargs):
            if tool_to_call.function.name == "kubectl_get":
                return _make_tool_call_result(tool_call_id=tool_to_call.id)
            elif tool_to_call.function.name == "show_chart":
                # Simulate what _invoke_llm_tool_call returns for FrontendPauseTool
                params = json.loads(tool_to_call.function.arguments)
                return ToolCallResult(
                    tool_call_id=tool_to_call.id,
                    tool_name="show_chart",
                    description="show_chart(params)",
                    result=StructuredToolResult(
                        status=StructuredToolResultStatus.FRONTEND_PAUSE,
                        params=params,
                    ),
                )
            raise ValueError(f"Unexpected tool: {tool_to_call.function.name}")

        ai._invoke_llm_tool_call = MagicMock(side_effect=_route_tool_call)

        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Check pods and show chart"}],
            )
        )

        # Backend tool should have a TOOL_RESULT
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].data["tool_name"] == "kubectl_get"

        # Frontend tool should cause APPROVAL_REQUIRED
        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 1
        assert len(approval_events[0].data["pending_frontend_tool_calls"]) == 1

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_frontend_tool_resume_with_results(self, _mock_limit, make_ai, mock_llm):
        """After pause, client sends frontend_tool_results and stream resumes
        producing TOOL_RESULT events for the injected results."""
        from holmes.core.models import FrontendToolResult

        # Build messages as if we paused: assistant with pending_frontend tool call
        messages = [
            {"role": "user", "content": "Show chart"},
            {
                "role": "assistant",
                "content": "Let me show a chart",
                "tool_calls": [
                    {
                        "id": "ft_1",
                        "type": "function",
                        "function": {
                            "name": "show_chart",
                            "arguments": '{"chart_type": "line"}',
                        },
                        "pending_frontend": True,
                    }
                ],
            },
        ]

        # LLM response after resume (final answer)
        resp_final = _make_llm_response(content="Here is the chart analysis", tool_calls=None)
        mock_llm.completion.return_value = resp_final

        ai = make_ai()

        frontend_results = [
            FrontendToolResult(
                tool_call_id="ft_1",
                tool_name="show_chart",
                result='{"rendered": true, "points": 42}',
            )
        ]

        events = _collect_stream_events(
            ai.call_stream(
                msgs=messages,
                frontend_tool_results=frontend_results,
            )
        )

        # Should have TOOL_RESULT for the injected frontend result
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(tool_results) >= 1
        frontend_result = [
            tr for tr in tool_results if tr.data.get("tool_name") == "show_chart"
        ]
        assert len(frontend_result) == 1

        # Should have ANSWER_END (stream completed after resume)
        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1
        assert answer_ends[0].data["content"] == "Here is the chart analysis"

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_frontend_tool_definitions_added_to_tools(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """Frontend tool definitions are included in the tools list sent to LLM."""
        resp = _make_llm_response(content="No tools needed", tool_calls=None)
        mock_llm.completion.return_value = resp

        ai = _make_ai_with_frontend_tools(make_ai, mock_tool_executor)
        _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "hello"}],
            )
        )

        # Verify LLM was called with both backend and frontend tools
        call_kwargs = mock_llm.completion.call_args
        tools_sent = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        tool_names = [t["function"]["name"] for t in tools_sent]
        assert "kubectl_get" in tool_names, "Backend tool should be included"
        assert "show_chart" in tool_names, "Frontend tool should be included"

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_approval_required_event_shape_with_frontend_tools(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """APPROVAL_REQUIRED event has the exact expected key set when triggered by frontend tools."""
        ft_call = _make_mock_tool_call(
            tool_call_id="ft_shape", tool_name="show_chart",
            arguments={"chart_type": "pie"},
        )
        resp = _make_llm_response(content="charting", tool_calls=[ft_call])
        mock_llm.completion.return_value = resp

        ai = _make_ai_with_frontend_tools(make_ai, mock_tool_executor)
        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "show chart"}],
            )
        )

        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 1
        data = approval_events[0].data

        # Same keys as regular approval_required — wire protocol is identical
        assert set(data.keys()) == EXPECTED_APPROVAL_REQUIRED_KEYS
        assert isinstance(data["pending_frontend_tool_calls"], list)
        assert isinstance(data["pending_approvals"], list)
        # Backend approvals empty, frontend has one entry
        assert len(data["pending_approvals"]) == 0
        assert len(data["pending_frontend_tool_calls"]) == 1
        assert set(data["costs"].keys()) == EXPECTED_COSTS_KEYS

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_pending_frontend_marked_in_messages(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """After frontend tool pause, the conversation messages contain
        the pending_frontend flag on the tool call."""
        ft_call = _make_mock_tool_call(
            tool_call_id="ft_mark", tool_name="show_chart",
            arguments={"chart_type": "line"},
        )
        resp = _make_llm_response(content="charting", tool_calls=[ft_call])
        mock_llm.completion.return_value = resp

        ai = _make_ai_with_frontend_tools(make_ai, mock_tool_executor)
        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "chart"}],
            )
        )

        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        messages = approval_events[0].data["messages"]

        # Find the assistant message with tool_calls
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) >= 1
        tool_calls_in_msg = assistant_msgs[-1].get("tool_calls", [])
        assert len(tool_calls_in_msg) >= 1

        # The tool call should be marked pending_frontend
        ft_tc = [tc for tc in tool_calls_in_msg if tc["id"] == "ft_mark"]
        assert len(ft_tc) == 1
        assert ft_tc[0].get("pending_frontend") is True


# ---------------------------------------------------------------------------
# Test: Frontend noop tools (fire-and-forget, no pause)
# ---------------------------------------------------------------------------


def _make_ai_with_noop_tools(make_ai_fn, mock_tool_executor, tool_names=None):
    """Create a ToolCallingLLM with FrontendNoopTool(s) injected into the executor."""
    from holmes.core.tools_utils.frontend_tools import build_frontend_noop_tool
    from holmes.core.tools_utils.tool_executor import ToolExecutor

    if tool_names is None:
        tool_names = [("navigate_to_page", "Navigate user to a page", {
            "type": "object",
            "properties": {
                "page": {"type": "string"},
            },
        }, None)]

    noop_tools = [
        build_frontend_noop_tool(name=name, description=desc, parameters=params, canned_response=resp)
        for name, desc, params, resp in tool_names
    ]

    clone = object.__new__(ToolExecutor)
    mock_toolset = MagicMock()
    mock_toolset.name = "kubectl"
    clone.toolsets = [mock_toolset]
    clone.enabled_toolsets = [mock_toolset]
    clone.tools_by_name = {}
    clone._tool_to_toolset = {}

    for nt in noop_tools:
        clone.tools_by_name[nt.name] = nt

    backend_tools = mock_tool_executor.get_all_tools_openai_format.return_value or []
    noop_openai = [nt.get_openai_format() for nt in noop_tools]
    clone.get_all_tools_openai_format = MagicMock(
        return_value=backend_tools + noop_openai
    )
    clone.ensure_toolset_initialized = MagicMock(return_value=None)

    ai = make_ai_fn()
    ai.tool_executor = clone
    return ai


class TestFrontendNoopToolFlow:
    """Test noop-mode frontend tools: LLM calls a noop tool, gets canned
    response immediately, stream continues without pausing."""

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_noop_tool_does_not_pause(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """When LLM calls a noop tool, stream does NOT emit approval_required
        and instead continues to ai_answer_end."""
        noop_call = _make_mock_tool_call(
            tool_call_id="noop_1", tool_name="navigate_to_page",
            arguments={"page": "/dashboards/cpu"},
        )
        # LLM iteration 1: calls the noop tool
        resp1 = _make_llm_response(content="Let me navigate you", tool_calls=[noop_call])
        # LLM iteration 2: final answer after seeing the canned response
        resp2 = _make_llm_response(content="Done, you're on the CPU dashboard", tool_calls=None)
        mock_llm.completion.side_effect = [resp1, resp2]

        ai = _make_ai_with_noop_tools(make_ai, mock_tool_executor)
        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Go to CPU dashboard"}],
            )
        )

        # Should NOT have APPROVAL_REQUIRED
        approval_events = _events_of_type(events, StreamEvents.APPROVAL_REQUIRED)
        assert len(approval_events) == 0

        # Should have TOOL_RESULT with the canned response
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].data["name"] == "navigate_to_page"
        result = tool_results[0].data["result"]
        assert result["status"] == "success"
        assert "successfully" in result["data"].lower()

        # Should have ANSWER_END
        answer_ends = _events_of_type(events, StreamEvents.ANSWER_END)
        assert len(answer_ends) == 1
        assert answer_ends[0].data["content"] == "Done, you're on the CPU dashboard"

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_noop_tool_custom_response(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """Noop tool with custom canned_response returns that response."""
        custom = "Chart rendered at /charts/overview.png"
        noop_call = _make_mock_tool_call(
            tool_call_id="noop_custom", tool_name="render_chart_noop",
            arguments={"chart_type": "line"},
        )
        resp1 = _make_llm_response(content="Rendering", tool_calls=[noop_call])
        resp2 = _make_llm_response(content="Chart is ready", tool_calls=None)
        mock_llm.completion.side_effect = [resp1, resp2]

        ai = _make_ai_with_noop_tools(make_ai, mock_tool_executor, tool_names=[
            ("render_chart_noop", "Render a chart", {"type": "object", "properties": {"chart_type": {"type": "string"}}}, custom),
        ])
        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Show chart"}],
            )
        )

        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].data["result"]["data"] == custom

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_noop_tool_visible_in_sse_events(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """Client sees start_tool_calling and tool_calling_result for noop tools."""
        noop_call = _make_mock_tool_call(
            tool_call_id="noop_vis", tool_name="navigate_to_page",
            arguments={"page": "/alerts"},
        )
        resp1 = _make_llm_response(content="Navigating", tool_calls=[noop_call])
        resp2 = _make_llm_response(content="Done", tool_calls=None)
        mock_llm.completion.side_effect = [resp1, resp2]

        ai = _make_ai_with_noop_tools(make_ai, mock_tool_executor)
        events = _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "Go to alerts"}],
            )
        )

        # start_tool_calling should be emitted
        start_events = _events_of_type(events, StreamEvents.START_TOOL)
        assert len(start_events) == 1
        assert start_events[0].data["tool_name"] == "navigate_to_page"
        assert start_events[0].data["id"] == "noop_vis"

        # tool_calling_result should be emitted
        tool_results = _events_of_type(events, StreamEvents.TOOL_RESULT)
        assert len(tool_results) == 1
        assert tool_results[0].data["tool_call_id"] == "noop_vis"

    @patch(LIMIT_PATCH, side_effect=_make_context_limiter_passthrough)
    def test_noop_tool_included_in_tools_list(self, _mock_limit, make_ai, mock_llm, mock_tool_executor):
        """Noop tool definitions are included in the tools list sent to LLM."""
        resp = _make_llm_response(content="No tools needed", tool_calls=None)
        mock_llm.completion.return_value = resp

        ai = _make_ai_with_noop_tools(make_ai, mock_tool_executor)
        _collect_stream_events(
            ai.call_stream(
                msgs=[{"role": "user", "content": "hello"}],
            )
        )

        call_kwargs = mock_llm.completion.call_args
        tools_sent = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        tool_names = [t["function"]["name"] for t in tools_sent]
        assert "kubectl_get" in tool_names, "Backend tool should be included"
        assert "navigate_to_page" in tool_names, "Noop tool should be included"
