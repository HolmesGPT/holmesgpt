"""Tests for recovering tool calls a model emitted as XML text (ROB-558)."""

import json
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import DefaultLLM
from holmes.core.tool_call_recovery import recover_tool_calls_from_text

TRIAGE = "update_ai_triage_metadata"
OFFERED = {TRIAGE}

# The modern Anthropic tool-use dialect a confused model narrates as text.
MODERN_XML = (
    "<function_calls>\n"
    f'<invoke name="{TRIAGE}">\n'
    '<parameter name="team">payments</parameter>\n'
    '<parameter name="team_reason">Owns the checkout service.</parameter>\n'
    '<parameter name="root_cause_analysis"># RCA\n'
    "The pod OOMed because `limit < usage`.</parameter>\n"
    "</invoke>\n"
    "</function_calls>"
)


class TestRecoverToolCallsFromText:
    def test_recovers_modern_anthropic_xml(self):
        cleaned, calls = recover_tool_calls_from_text(MODERN_XML, OFFERED)
        assert len(calls) == 1
        assert calls[0].function.name == TRIAGE
        args = json.loads(calls[0].function.arguments)
        assert args["team"] == "payments"
        assert args["team_reason"] == "Owns the checkout service."
        assert args["root_cause_analysis"].startswith("# RCA")
        assert "limit < usage" in args["root_cause_analysis"]
        # The XML is stripped from the visible answer.
        assert cleaned in (None, "")

    def test_recovers_with_mismatched_closing_tags(self):
        """The real ROB-558 payload: values closed with `</key>` (or the next
        `<parameter>` opens before the previous one closes), not `</parameter>`.
        A strict XML parser breaks on this; the lenient one must not."""
        content = (
            f'<invoke name="{TRIAGE}">'
            '<parameter name="urgency_reason">High blast radius'
            "</urgency_reason> "
            '<parameter name="team">platform</parameter>'
            "</invoke>"
        )
        cleaned, calls = recover_tool_calls_from_text(content, OFFERED)
        assert len(calls) == 1
        args = json.loads(calls[0].function.arguments)
        assert args["urgency_reason"] == "High blast radius"
        assert args["team"] == "platform"
        assert cleaned in (None, "")

    def test_value_with_xml_and_ampersand_that_breaks_strict_parsers(self):
        content = (
            f'<invoke name="{TRIAGE}">'
            '<parameter name="root_cause_analysis">Config had `a < b && c > d` '
            "and a `<div>` tag in the log.</parameter>"
            "</invoke>"
        )
        _, calls = recover_tool_calls_from_text(content, OFFERED)
        assert len(calls) == 1
        args = json.loads(calls[0].function.arguments)
        assert "a < b && c > d" in args["root_cause_analysis"]
        assert "<div>" in args["root_cause_analysis"]

    def test_preserves_leading_prose(self):
        content = "Recording the triage decision now.\n" + MODERN_XML
        cleaned, calls = recover_tool_calls_from_text(content, OFFERED)
        assert len(calls) == 1
        assert cleaned == "Recording the triage decision now."

    def test_no_recovery_when_tool_not_offered(self):
        cleaned, calls = recover_tool_calls_from_text(MODERN_XML, {"some_other_tool"})
        assert calls == []
        assert cleaned == MODERN_XML

    def test_no_recovery_for_plain_answer(self):
        content = "The pod is CrashLooping because of an OOM kill."
        cleaned, calls = recover_tool_calls_from_text(content, OFFERED)
        assert calls == []
        assert cleaned == content

    def test_no_recovery_without_offered_tools(self):
        cleaned, calls = recover_tool_calls_from_text(MODERN_XML, set())
        assert calls == []
        assert cleaned == MODERN_XML

    def test_none_content(self):
        cleaned, calls = recover_tool_calls_from_text(None, OFFERED)
        assert calls == []
        assert cleaned is None

    def test_recovers_multiple_invocations(self):
        content = (
            f'<invoke name="{TRIAGE}"><parameter name="team">a</parameter></invoke>'
            f'<invoke name="{TRIAGE}"><parameter name="team">b</parameter></invoke>'
        )
        _, calls = recover_tool_calls_from_text(content, OFFERED)
        assert [json.loads(c.function.arguments)["team"] for c in calls] == ["a", "b"]

    def test_old_tool_name_tag_dialect(self):
        content = (
            "<function_calls><invoke>"
            f"<tool_name>{TRIAGE}</tool_name>"
            '<parameter name="team">infra</parameter>'
            "</invoke></function_calls>"
        )
        _, calls = recover_tool_calls_from_text(content, OFFERED)
        assert len(calls) == 1
        assert calls[0].function.name == TRIAGE
        assert json.loads(calls[0].function.arguments)["team"] == "infra"

    def test_recovered_call_has_function_type_and_id(self):
        _, calls = recover_tool_calls_from_text(MODERN_XML, OFFERED)
        assert calls[0].type == "function"
        assert calls[0].id


def _make_llm(model: str = "bedrock/anthropic.claude") -> DefaultLLM:
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = model
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = {}
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = 200000
    return llm


def _response(content, tool_calls=None) -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-test",
        choices=[
            Choices(
                index=0,
                message=Message(
                    role="assistant", content=content, tool_calls=tool_calls
                ),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


TOOLS = [{"type": "function", "function": {"name": TRIAGE, "description": "d"}}]


class TestCompletionRecoveryIntegration:
    def test_completion_recovers_xml_tool_call(self):
        llm = _make_llm()
        with patch("holmes.core.llm.litellm.completion") as mock:
            mock.return_value = _response(MODERN_XML)
            result = llm.completion(
                messages=[{"role": "user", "content": "go"}],
                tools=TOOLS,
                tool_choice="auto",
            )
        msg = result.choices[0].message
        assert msg.tool_calls, "XML tool call should have been recovered"
        assert msg.tool_calls[0].function.name == TRIAGE
        assert not (msg.content or "").strip()

    def test_completion_leaves_structured_tool_calls_untouched(self):
        llm = _make_llm()
        structured = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": TRIAGE, "arguments": '{"team": "x"}'},
            }
        ]
        with patch("holmes.core.llm.litellm.completion") as mock:
            mock.return_value = _response("done", tool_calls=structured)
            result = llm.completion(
                messages=[{"role": "user", "content": "go"}],
                tools=TOOLS,
                tool_choice="auto",
            )
        msg = result.choices[0].message
        assert len(msg.tool_calls) == 1
        assert msg.content == "done"

    def test_completion_leaves_plain_answer_untouched(self):
        llm = _make_llm()
        with patch("holmes.core.llm.litellm.completion") as mock:
            mock.return_value = _response("Just a normal answer.")
            result = llm.completion(
                messages=[{"role": "user", "content": "go"}],
                tools=TOOLS,
                tool_choice="auto",
            )
        msg = result.choices[0].message
        assert not msg.tool_calls
        assert msg.content == "Just a normal answer."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
