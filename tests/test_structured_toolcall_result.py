import json

import pytest
from pydantic import BaseModel

from holmes.core.models import ToolCallResult, format_tool_result_data
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus


class DummyResult(BaseModel):
    x: int
    y: str


class Unserializable:
    def __str__(self):
        return "unserializable_str"


@pytest.mark.parametrize(
    "data,expected",
    [
        (None, ""),
        ("simple string", "simple string"),
    ],
)
def test_get_stringified_data_none_and_str(data, expected):
    result = StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=data)
    assert result.get_stringified_data() == expected


def test_get_stringified_data_base_model():
    dummy = DummyResult(x=10, y="hello")
    result = StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=dummy)
    expected = dummy.model_dump_json()
    assert result.get_stringified_data() == expected


@pytest.mark.parametrize(
    "data",
    [
        {"key": "value", "num": 5},
        [1, 2, 3],
    ],
)
def test_get_stringified_data_json_serializable(data):
    result = StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=data)
    expected = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    assert result.get_stringified_data() == expected


def test_get_stringified_data_unserializable_object():
    obj = Unserializable()
    result = StructuredToolResult(status=StructuredToolResultStatus.ERROR, data=obj)
    assert result.get_stringified_data() == "unserializable_str"


@pytest.mark.parametrize(
    "status,error,return_code,url,invocation,params",
    [
        (StructuredToolResultStatus.SUCCESS, None, None, None, None, None),
        (
            StructuredToolResultStatus.ERROR,
            "oops",
            1,
            "http://example.com",
            "invoke",
            {"a": 1},
        ),
    ],
)
def test_default_and_custom_fields(status, error, return_code, url, invocation, params):
    result = StructuredToolResult(
        status=status,
        error=error,
        return_code=return_code,
        data=None,
        url=url,
        invocation=invocation,
        params=params,
    )
    assert result.schema_version == "robusta:v1.0.0"
    assert result.status == status
    assert result.error == error
    assert result.return_code == return_code
    assert result.data is None
    assert result.url == url
    assert result.invocation == invocation
    assert result.params == params


@pytest.mark.parametrize(
    "status,error,data,expected",
    [
        # Non-error statuses return just the data (no metadata prefix)
        (StructuredToolResultStatus.SUCCESS, None, "test", "test"),
        (
            StructuredToolResultStatus.NO_DATA,
            None,
            DummyResult(x=2, y="test"),
            DummyResult(x=2, y="test").model_dump_json(),
        ),
        (
            StructuredToolResultStatus.SUCCESS,
            None,
            {"k": 1},
            json.dumps({"k": 1}, separators=(",", ":"), ensure_ascii=False),
        ),
        (
            StructuredToolResultStatus.SUCCESS,
            None,
            Unserializable(),
            str(Unserializable()),
        ),
    ],
)
def test_format_tool_result_data_non_error(status, error, data, expected):
    """Test that non-error results return clean data without metadata prefix."""
    tool_result = StructuredToolResult(status=status, error=error, data=data)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: no metadata prefix, just clean data
    assert format_tool_result_data(tool_result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_str_non_error():
    """Test string data returns clean output without metadata."""
    result = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="hello"
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: just the data, no metadata prefix
    expected = "hello"
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_base_model_non_error():
    """Test BaseModel data returns clean JSON output."""
    dummy = DummyResult(x=2, y="b")
    result = StructuredToolResult(status=StructuredToolResultStatus.NO_DATA, data=dummy)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: just the JSON data
    expected = dummy.model_dump_json()
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_json_serializable_non_error():
    """Test dict data returns clean JSON output."""
    data = {"k": 3}
    result = StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=data)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: just the JSON data
    expected = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_unserializable_non_error():
    """Test unserializable object returns str() output."""
    obj = Unserializable()
    result = StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=obj)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: just str(obj)
    expected = str(obj)
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_error_with_message_and_data():
    """Test error with message and data returns formatted error."""
    result = StructuredToolResult(
        status=StructuredToolResultStatus.ERROR, error="fail", data="oops"
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: Error: {message}\n{data}
    expected = "Error: fail\noops"
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_error_without_message_or_data():
    """Test error without message or data returns default error."""
    result = StructuredToolResult(
        status=StructuredToolResultStatus.ERROR, error=None, data=None
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: just the error message
    expected = "Error: Tool execution failed"
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_error_without_message_with_unserializable():
    """Test error without message but with data returns error + data."""
    obj = Unserializable()
    result = StructuredToolResult(
        status=StructuredToolResultStatus.ERROR, error=None, data=obj
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    # New behavior: Error: default message\ndata
    expected = f"Error: Tool execution failed\n{str(obj)}"
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_with_extra_metadata():
    """Test that extra_metadata is included when provided."""
    result = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="hello"
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    extra_metadata = {"bash_session_approved_prefixes": ["kubectl get"]}
    # With extra_metadata, the metadata prefix is included
    expected = f"tool_call_metadata={json.dumps(extra_metadata)}\nhello"
    assert (
        format_tool_result_data(result, tool_call_id, tool_name, extra_metadata)
        == expected
    )


def test_as_tool_call_message_without_params():
    """Test tool call message without params returns clean content."""
    structured = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="hello"
    )
    tcr = ToolCallResult(
        tool_call_id="call1",
        tool_name="toolX",
        description="desc",
        result=structured,
    )
    message = tcr.as_tool_call_message()
    # New behavior: just the data, no metadata prefix
    expected_content = "hello"
    assert message == {
        "tool_call_id": "call1",
        "role": "tool",
        "name": "toolX",
        "content": expected_content,
    }


def test_as_tool_call_message_with_params():
    """Test tool call message with params - params are NOT included in content anymore."""
    structured = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS,
        data="hello",
        params={"pod_name": "my-pod", "namespace": "my-namespace"},
    )
    tcr = ToolCallResult(
        tool_call_id="call1",
        tool_name="toolX",
        description="desc",
        result=structured,
    )
    message = tcr.as_tool_call_message()
    # New behavior: params are NOT included in content (they're in the StructuredToolResult)
    # This makes the content clean and grepable
    expected_content = "hello"
    assert message == {
        "tool_call_id": "call1",
        "role": "tool",
        "name": "toolX",
        "content": expected_content,
    }


def test_as_tool_call_message_with_extra_metadata():
    """Test tool call message with extra_metadata includes metadata prefix."""
    structured = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="hello"
    )
    tcr = ToolCallResult(
        tool_call_id="call1",
        tool_name="toolX",
        description="desc",
        result=structured,
    )
    extra_metadata = {"bash_session_approved_prefixes": ["kubectl get"]}
    message = tcr.as_tool_call_message(extra_metadata=extra_metadata)
    # With extra_metadata, the metadata prefix IS included
    expected_content = f"tool_call_metadata={json.dumps(extra_metadata)}\nhello"
    assert message == {
        "tool_call_id": "call1",
        "role": "tool",
        "name": "toolX",
        "content": expected_content,
    }


def test_as_tool_result_response():
    structured = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="hello"
    )
    tcr = ToolCallResult(
        tool_call_id="call1",
        tool_name="toolX",
        description="desc",
        result=structured,
    )
    response = tcr.as_tool_result_response()
    assert response["tool_call_id"] == "call1"
    assert response["tool_name"] == "toolX"
    assert response["description"] == "desc"
    assert response["role"] == "tool"

    expected_dump = structured.model_dump()
    expected_dump["data"] = structured.get_stringified_data()
    assert response["result"] == expected_dump


def test_as_streaming_tool_result_response():
    structured = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="hello"
    )
    tcr = ToolCallResult(
        tool_call_id="call2",
        tool_name="toolY",
        description="desc2",
        result=structured,
    )
    streaming = tcr.as_streaming_tool_result_response()
    assert streaming["tool_call_id"] == "call2"
    assert streaming["role"] == "tool"
    assert streaming["description"] == "desc2"
    assert streaming["name"] == "toolY"

    expected_dump = structured.model_dump()
    expected_dump["data"] = structured.get_stringified_data()
    assert streaming["result"] == expected_dump
