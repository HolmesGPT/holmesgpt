"""A trailing frontend tool call must not discard the answer the model already gave.

See #2428: when the model produced content AND only frontend tool calls (SuggestSkills
and friends), the loop used to emit a content-less APPROVAL_REQUIRED and return. The
client resumes, the model re-answers, and the regenerated answer is a shorter version
of the one it already produced -- so the detail that would have been graded is gone.

The fix makes that case terminal: emit ANSWER_END with the existing content instead of
pausing. Genuine approvals still pause.
"""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from holmes.core.llm import LLM, ContextWindowUsage
from holmes.core.models import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools_utils.tool_executor import ToolExecutor
from holmes.utils.stream import StreamEvents

ANSWER = (
    "The crash is a liveness probe failing on /healthz: the pod is ready but the "
    "endpoint returns 503 because the dependency it awaits is still migrating. "
    "Three distinct contributing factors, in order of impact: (1) the readiness gate "
    "does not cover the dependency, (2) the timeout is below the observed p99, and "
    "(3) the retry budget is exhausted after the first blip. Fix (1) first."
)


def _mock_llm_response(content: str, tool_calls=None):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message = MagicMock()
    mock_response.choices[0].message.content = content
    mock_response.choices[0].message.tool_calls = tool_calls
    mock_response.choices[0].message.reasoning_content = None
    mock_response.choices[0].finish_reason = "tool_calls" if tool_calls else "stop"
    # The loop appends this dict to `messages` and later looks tool calls up in it,
    # so it has to carry the calls the same way a real assistant message does.
    mock_response.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in (tool_calls or [])
        ]
        or None,
    }
    return mock_response


def _mock_tool_call(tool_call_id="tc_suggest", tool_name="SuggestSkills"):
    tc = MagicMock()
    tc.id = tool_call_id
    tc.function = MagicMock()
    tc.function.name = tool_name
    tc.function.arguments = '{"skills": ["kubernetes"]}'
    return tc


def _build_ai(invoke_result: StructuredToolResult) -> ToolCallingLLM:
    mock_llm = MagicMock(spec=LLM)
    mock_llm.count_tokens.return_value = ContextWindowUsage(
        total_tokens=100,
        system_tokens=0,
        tools_to_call_tokens=0,
        tools_tokens=0,
        user_tokens=0,
        assistant_tokens=0,
        other_tokens=0,
    )
    mock_llm.get_context_window_size.return_value = 128000
    mock_llm.get_maximum_output_token.return_value = 4096
    mock_llm.get_max_token_count_for_single_tool.return_value = 20000
    mock_llm.model = "gpt-4o"

    mock_tool_executor = MagicMock(spec=ToolExecutor)
    mock_tool_executor.get_all_tools_openai_format.return_value = [
        {
            "type": "function",
            "function": {
                "name": "SuggestSkills",
                "description": "Suggest skills",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    mock_toolset = MagicMock()
    mock_toolset.name = "skills"
    mock_toolset.status = MagicMock()
    mock_toolset.status.value = "enabled"
    mock_tool_executor.toolsets = [mock_toolset]

    ai = ToolCallingLLM(
        tool_executor=mock_tool_executor,
        max_steps=5,
        llm=mock_llm,
        tool_results_dir=None,
    )

    def mock_invoke_tool(
        tool_name: str,
        tool_params: dict,
        user_approved: bool,
        tool_call_id: str,
        tool_number: Optional[int] = None,
        session_approved_prefixes: Optional[List[str]] = None,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> StructuredToolResult:
        return invoke_result

    ai._directly_invoke_tool_call = mock_invoke_tool
    return ai, mock_llm


def _stream(ai, question: str):
    """Drive call_stream the way the server does (one user message, approvals on)."""
    return ai.call_stream(
        msgs=[{"role": "user", "content": question}],
        enable_tool_approval=True,
    )


def _drain(stream) -> List[Any]:
    return list(stream)


def test_content_plus_frontend_only_call_is_terminal_not_a_pause():
    """#2428: answer + SuggestSkills must come back as ANSWER_END with the full text."""
    ai, mock_llm = _build_ai(
        StructuredToolResult(
            status=StructuredToolResultStatus.FRONTEND_PAUSE,
            data=None,
            params={"skills": ["kubernetes"]},
        )
    )
    mock_llm.completion.return_value = _mock_llm_response(
        ANSWER, tool_calls=[_mock_tool_call()]
    )

    events = _drain(_stream(ai, "why is the pod crashing?"))
    emitted = [(e.event, e.data) for e in events]

    kinds = [e[0] for e in emitted]
    assert StreamEvents.ANSWER_END.value in kinds, kinds
    assert StreamEvents.APPROVAL_REQUIRED.value not in kinds, (
        "a frontend-only side effect must not pause the turn: "
        f"{kinds}"
    )

    answer = next(d for e, d in emitted if e == StreamEvents.ANSWER_END.value)
    # The whole answer survives -- this is the regression the issue is about.
    assert answer["content"] == ANSWER
    assert "Three distinct contributing factors" in answer["content"]
    assert answer["content"] is not None


def test_a_real_approval_still_pauses_even_with_content():
    """The fix must not swallow genuine approvals -- those still need a human."""
    ai, mock_llm = _build_ai(
        StructuredToolResult(
            status=StructuredToolResultStatus.APPROVAL_REQUIRED,
            data="needs a human",
            params={"command": "kubectl delete pod x"},
        )
    )
    mock_llm.completion.return_value = _mock_llm_response(
        "About to delete a pod", tool_calls=[_mock_tool_call(tool_name="kubectl_delete")]
    )

    events = _drain(_stream(ai, "delete the pod"))
    kinds = [e.event for e in events]

    assert StreamEvents.APPROVAL_REQUIRED.value in kinds, kinds
    assert StreamEvents.ANSWER_END.value not in kinds, (
        "a pending approval must still pause instead of being reported as final"
    )


def test_frontend_pause_without_content_still_pauses():
    """No content yet (model asked to call a tool first) -- pause as before."""
    ai, mock_llm = _build_ai(
        StructuredToolResult(
            status=StructuredToolResultStatus.FRONTEND_PAUSE,
            data=None,
            params={},
        )
    )
    mock_llm.completion.return_value = _mock_llm_response(
        "", tool_calls=[_mock_tool_call()]
    )

    events = _drain(_stream(ai, "suggest skills"))
    kinds = [e.event for e in events]

    assert StreamEvents.APPROVAL_REQUIRED.value in kinds, kinds
    assert StreamEvents.ANSWER_END.value not in kinds


def test_approval_plus_frontend_call_still_pauses():
    """When both are pending, the human still has to decide -- never terminal."""
    def both(tool_name, tool_params, user_approved, tool_call_id, **kw):
        if tool_call_id == "tc_approve":
            return StructuredToolResult(
                status=StructuredToolResultStatus.APPROVAL_REQUIRED,
                data="needs a human",
                params=tool_params,
            )
        return StructuredToolResult(
            status=StructuredToolResultStatus.FRONTEND_PAUSE, data=None, params={}
        )

    ai, mock_llm = _build_ai(None)
    ai._directly_invoke_tool_call = both
    mock_llm.completion.return_value = _mock_llm_response(
        "Here is the answer, and now deleting",
        tool_calls=[
            _mock_tool_call(tool_call_id="tc_approve", tool_name="kubectl_delete"),
            _mock_tool_call(tool_call_id="tc_suggest", tool_name="SuggestSkills"),
        ],
    )

    events = _drain(_stream(ai, "do both"))
    kinds = [e.event for e in events]

    assert StreamEvents.APPROVAL_REQUIRED.value in kinds, kinds
    assert StreamEvents.ANSWER_END.value not in kinds
