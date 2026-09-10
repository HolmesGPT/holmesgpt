"""Tests for the Gemini two-phase health check path.

Gemini (Google AI Studio and Vertex-AI Gemini) rejects GenerateContent
requests that combine function calling (tools) with a JSON-schema
response_format. Health checks always run with tools enabled, so for Gemini
models the check is split into two phases:

1. Investigate with tools and no response_format.
2. Coerce the findings into the {rationale, passed} schema with a tools-free
   structured call.

Other providers keep the single-call path.
"""

import json
from unittest.mock import MagicMock

from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.checks.checks import CHECK_RESPONSE_FORMAT, _execute_ai_check
from holmes.checks.models import Check, CheckMode
from holmes.core.tool_calling_llm import LLMResult


def _make_check() -> Check:
    return Check(
        name="test-check",
        query="Are all pods running in the default namespace?",
        timeout=30,
        mode=CheckMode.MONITOR,
        destinations=[],
    )


def _make_model_response(content: str) -> ModelResponse:
    """Build a minimal litellm ModelResponse for the coerce phase."""
    return ModelResponse(
        choices=[
            Choices(
                message=Message(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def test_gemini_uses_two_phase_execution():
    """Gemini models investigate without response_format, then coerce with a
    tools-free structured call."""
    ai = MagicMock()
    ai.llm.model = "vertex_ai/gemini-2.5-pro"

    investigation = LLMResult(
        result="All pods in the default namespace are Running.",
        tool_calls=[],
        num_llm_calls=2,
        messages=[{"role": "assistant", "content": "..."}],
        finish_reason="tool_calls",
        total_cost=0.01,
        total_tokens=100,
        prompt_tokens=80,
        completion_tokens=20,
    )
    ai.call.return_value = investigation

    coerce_json = json.dumps(
        {"passed": True, "rationale": "All pods are Running, no problem found."}
    )
    ai.llm.completion.return_value = _make_model_response(coerce_json)

    result = _execute_ai_check(_make_check(), ai)

    # Phase 1: ai.call invoked WITHOUT response_format (Gemini forbids tools + schema).
    assert ai.call.call_count == 1
    _, call_kwargs = ai.call.call_args
    assert "response_format" not in call_kwargs

    # Phase 2: tools-free structured completion carries the response_format.
    assert ai.llm.completion.call_count == 1
    completion_kwargs = ai.llm.completion.call_args.kwargs
    assert completion_kwargs["tools"] is None
    assert completion_kwargs["response_format"] == CHECK_RESPONSE_FORMAT

    # Final result carries the structured phase-2 content...
    assert json.loads(result.result) == {
        "passed": True,
        "rationale": "All pods are Running, no problem found.",
    }
    # ...and the tool calls / message history from the investigation.
    assert result.messages == investigation.messages
    # finish_reason reflects the phase-2 coerce call, not the investigation.
    assert result.finish_reason == "stop"
    # Costs from both phases are summed.
    assert result.total_cost == 0.01
    assert result.total_tokens == 115  # 100 (phase 1) + 15 (phase 2)
    # num_llm_calls includes the extra coerce call.
    assert result.num_llm_calls == 3


def test_non_gemini_uses_single_call_with_response_format():
    """Non-Gemini models pass response_format inline in a single call."""
    ai = MagicMock()
    ai.llm.model = "anthropic/claude-sonnet-4-5-20250929"

    single = LLMResult(
        result=json.dumps({"passed": True, "rationale": "Healthy."}),
        tool_calls=[],
    )
    ai.call.return_value = single

    result = _execute_ai_check(_make_check(), ai)

    # Single call with the structured response_format; no separate coerce call.
    assert ai.call.call_count == 1
    _, call_kwargs = ai.call.call_args
    assert call_kwargs["response_format"] == CHECK_RESPONSE_FORMAT
    ai.llm.completion.assert_not_called()

    assert result is single
