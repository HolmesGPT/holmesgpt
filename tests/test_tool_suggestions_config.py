"""Unit tests for the SUGGEST_RUNBOOKS wiring used by LLM evals."""

from __future__ import annotations

import json

from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from tests.llm.utils.tool_suggestions_config import (
    SUGGEST_RUNBOOKS_NOOP_RESPONSE,
    SUGGEST_RUNBOOKS_SYSTEM_PROMPT,
    SUGGEST_RUNBOOKS_TOOL_NAME,
    append_suggest_runbooks_system_prompt,
    extract_suggested_memories,
)


def test_append_system_prompt_standalone():
    assert append_suggest_runbooks_system_prompt(None) == SUGGEST_RUNBOOKS_SYSTEM_PROMPT


def test_append_system_prompt_extends_existing():
    appended = append_suggest_runbooks_system_prompt("EXISTING")
    assert appended.startswith("EXISTING")
    assert SUGGEST_RUNBOOKS_SYSTEM_PROMPT in appended


def _make_tool_call(tool_name: str, params):
    return ToolCallResult(
        tool_call_id="x",
        tool_name=tool_name,
        description=f"{tool_name}({json.dumps(params)})",
        result=StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=SUGGEST_RUNBOOKS_NOOP_RESPONSE,
            params=params,
        ),
    )


def test_extract_suggested_memories_from_params():
    payload = {
        "suggestions": [
            {
                "title": "Querying checkout-service metrics uses non-default label",
                "when_to_use": "Any PromQL for checkout-service in this cluster",
                "failed_call": (
                    'PromQL: sum(rate(http_requests_total{app="checkout"}[5m]))'
                    " — returned empty"
                ),
                "working_call": (
                    "PromQL: sum(rate(http_requests_total"
                    '{service.team/component="checkout"}[5m]))'
                ),
                "why_env_specific": (
                    "This team overrides the default app= label with their"
                    " own taxonomy"
                ),
                "importance": "high",
            }
        ]
    }
    tcr = _make_tool_call(SUGGEST_RUNBOOKS_TOOL_NAME, payload)
    memories = extract_suggested_memories([tcr])
    assert len(memories) == 1
    assert "checkout-service" in memories[0]["title"]
    assert "service.team/component" in memories[0]["working_call"]


def test_extract_suggested_memories_ignores_other_tools():
    other = _make_tool_call("kubectl_get", {"resource": "pods"})
    assert extract_suggested_memories([other]) == []


def test_extract_suggested_memories_handles_none_and_empty():
    assert extract_suggested_memories(None) == []
    assert extract_suggested_memories([]) == []


def test_extract_suggested_memories_falls_back_to_description():
    """When ``result.params`` is missing, parse the JSON from the description."""
    payload = {
        "suggestions": [
            {
                "title": "fallback",
                "when_to_use": "w",
                "failed_call": "f",
                "working_call": "wc",
                "why_env_specific": "y",
                "importance": "low",
            }
        ]
    }

    class FakeResult:
        params = None

    class FakeToolCall:
        tool_name = SUGGEST_RUNBOOKS_TOOL_NAME
        description = f"{SUGGEST_RUNBOOKS_TOOL_NAME}({json.dumps(payload)})"
        result = FakeResult()
        params = None

    memories = extract_suggested_memories([FakeToolCall()])
    assert [m["title"] for m in memories] == ["fallback"]
