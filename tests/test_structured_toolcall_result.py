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
    tool_result = StructuredToolResult(status=status, error=error, data=data)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    metadata_prefix = f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}'
    assert (
        format_tool_result_data(tool_result, tool_call_id, tool_name)
        == metadata_prefix + expected
    )


def test_format_tool_result_data_str_non_error():
    result = StructuredToolResult(
        status=StructuredToolResultStatus.SUCCESS, data="hello"
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    expected = f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}hello'
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_base_model_non_error():
    dummy = DummyResult(x=2, y="b")
    result = StructuredToolResult(status=StructuredToolResultStatus.NO_DATA, data=dummy)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    expected = (
        f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}'
        + dummy.model_dump_json()
    )
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_json_serializable_non_error():
    data = {"k": 3}
    result = StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=data)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    expected = (
        f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}'
        + json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    )
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_unserializable_non_error():
    obj = Unserializable()
    result = StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data=obj)
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    expected = (
        f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}'
        + str(obj)
    )
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_error_with_message_and_data():
    result = StructuredToolResult(
        status=StructuredToolResultStatus.ERROR, error="fail", data="oops"
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    expected = f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}fail:\n\noops'
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_error_without_message_or_data():
    result = StructuredToolResult(
        status=StructuredToolResultStatus.ERROR, error=None, data=None
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    expected = f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}Tool execution failed:\n\n'
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_format_tool_result_data_error_without_message_with_unserializable():
    obj = Unserializable()
    result = StructuredToolResult(
        status=StructuredToolResultStatus.ERROR, error=None, data=obj
    )
    tool_call_id = "test_call_123"
    tool_name = "test_tool"
    metadata_prefix = f'tool_call_metadata={{"tool_name": "{tool_name}", "tool_call_id": "{tool_call_id}"}}'
    expected = f"{metadata_prefix}Tool execution failed:\n\n{str(obj)}"
    assert format_tool_result_data(result, tool_call_id, tool_name) == expected


def test_as_tool_call_message_without_params():
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
    expected_content = (
        'tool_call_metadata={"tool_name": "toolX", "tool_call_id": "call1"}hello'
    )
    assert message == {
        "tool_call_id": "call1",
        "role": "tool",
        "name": "toolX",
        "content": expected_content,
    }


def test_as_tool_call_message_with_params():
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
    expected_content = (
        'Params used for the tool call: {"pod_name": "my-pod", "namespace": "my-namespace"}. The tool call output follows on the next line.\n'
        'tool_call_metadata={"tool_name": "toolX", "tool_call_id": "call1"}hello'
    )
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
