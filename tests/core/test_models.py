import json

import pytest
from pydantic import ValidationError

from holmes.core.models import ChatRequest, ChatRequestBaseModel, ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


class TestToolCallResultUrlInLlmMessage:
    """Regression coverage for #2289: StructuredToolResult.url must reach the LLM."""

    def test_to_llm_message_includes_result_url(self):
        url = "https://logs.example.com/select/vmui/#/?query=%2A"
        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=[{"_msg": "sample"}],
            params={"query": "*"},
            url=url,
        )
        call = ToolCallResult(
            tool_call_id="test",
            tool_name="victorialogs_query",
            description="query",
            result=result,
        )

        assert call.to_client_dict()["result"]["url"] == url
        llm_content = call.to_llm_message()["content"]
        assert isinstance(llm_content, str)
        assert url in llm_content
        assert f"Result URL: {url}" in llm_content

    def test_to_llm_message_omits_result_url_when_absent(self):
        result = StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=[{"_msg": "sample"}],
        )
        call = ToolCallResult(
            tool_call_id="test",
            tool_name="some_tool",
            description="query",
            result=result,
        )

        llm_content = call.to_llm_message()["content"]
        assert isinstance(llm_content, str)
        assert "Result URL:" not in llm_content


class TestCheckFirstItemRole:
    """Tests for the check_first_item_role mode='before' validator.

    Regression coverage for "'bytes' object has no attribute 'get'": FastAPI
    hands the validator the raw request body (bytes/str) instead of a parsed
    dict when the client omits the Content-Type: application/json header.
    """

    def test_dict_input_with_system_role_first(self):
        model = ChatRequestBaseModel.model_validate(
            {
                "conversation_history": [
                    {"role": "system", "content": "you are a helpful assistant"},
                    {"role": "user", "content": "hi"},
                ]
            }
        )
        assert model.conversation_history[0]["role"] == "system"

    def test_dict_input_without_system_role_first_raises(self):
        with pytest.raises(ValidationError, match="must contain 'role': 'system'"):
            ChatRequestBaseModel.model_validate(
                {"conversation_history": [{"role": "user", "content": "hi"}]}
            )

    def test_bytes_json_body_is_parsed(self):
        """A JSON body sent as raw bytes (missing Content-Type) must still validate."""
        raw = json.dumps(
            {
                "conversation_history": [
                    {"role": "system", "content": "sys"},
                ]
            }
        ).encode("utf-8")
        model = ChatRequestBaseModel.model_validate(raw)
        assert model.conversation_history[0]["role"] == "system"

    def test_str_json_body_is_parsed(self):
        raw = json.dumps({"model": "gpt-5.4"})
        model = ChatRequestBaseModel.model_validate(raw)
        assert model.model == "gpt-5.4"

    def test_bytes_json_body_enforces_system_role(self):
        raw = json.dumps(
            {"conversation_history": [{"role": "user", "content": "hi"}]}
        ).encode("utf-8")
        with pytest.raises(ValidationError, match="must contain 'role': 'system'"):
            ChatRequestBaseModel.model_validate(raw)

    def test_non_json_bytes_raises_validation_error_not_attribute_error(self):
        """Garbage (non-JSON) bytes must produce a clean ValidationError, not a 500."""
        with pytest.raises(ValidationError):
            ChatRequestBaseModel.model_validate(b"not json at all")

    def test_non_dict_first_item_raises_validation_error_not_attribute_error(self):
        """A conversation_history whose first item is not a dict must produce a
        clean ValidationError, not an AttributeError from first_item.get()."""
        with pytest.raises(ValidationError):
            ChatRequestBaseModel.model_validate({"conversation_history": ["bad"]})

    def test_chat_request_from_bytes_body(self):
        """End-to-end: the public ChatRequest model also tolerates a bytes body."""
        raw = json.dumps(
            {
                "ask": "why is my pod crashing?",
                "conversation_history": [{"role": "system", "content": "sys"}],
            }
        ).encode("utf-8")
        model = ChatRequest.model_validate(raw)
        assert model.ask == "why is my pod crashing?"
