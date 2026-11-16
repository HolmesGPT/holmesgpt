import copy
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.llm import DefaultLLM, _relax_strict_schema_for_groq
from holmes.core.openai_formatting import format_tool_to_open_ai_standard
from holmes.core.tools import ToolParameter

NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}

# The arguments Groq rejected in #2217 ("missing properties: 'start_time',
# 'end_time', 'exclude_filter'"): the model left the optional fields out.
ISSUE_ARGUMENTS = {
    "namespace": "XXX",
    "pod_name": "XXX",
    "filter": "(?i)error",
    "limit": 100,
}

GROQ_ROUTES = [
    ("groq/openai/gpt-oss-120b", {}),
    ("groq/llama-3.3-70b-versatile", {}),
    ("llama-3.3-70b-versatile", {"custom_llm_provider": "groq"}),
]


def _mock_model_response() -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-test",
        choices=[
            Choices(
                index=0,
                message=Message(role="assistant", content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _make_llm(model: str, args: dict | None = None) -> DefaultLLM:
    llm = DefaultLLM.__new__(DefaultLLM)
    llm.model = model
    llm.api_key = None
    llm.api_base = None
    llm.api_version = None
    llm.args = args or {}
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = None
    return llm


def _fetch_pod_logs_tool() -> dict:
    """A tool with fetch_pod_logs' parameters, formatted the way Holmes sends it."""
    return format_tool_to_open_ai_standard(
        "fetch_pod_logs",
        "Fetch logs for a Kubernetes pod",
        {
            "pod_name": ToolParameter(type="string", required=True),
            "namespace": ToolParameter(type="string", required=True),
            "start_time": ToolParameter(type="string", required=False),
            "end_time": ToolParameter(type="string", required=False),
            "limit": ToolParameter(type="integer", required=False),
            "filter": ToolParameter(type="string", required=False),
            "exclude_filter": ToolParameter(type="string", required=False),
        },
    )


def _nested_tool() -> dict:
    return format_tool_to_open_ai_standard(
        "query",
        "Query targets",
        {
            "selector": ToolParameter(
                type="object",
                required=True,
                properties={
                    "app": ToolParameter(type="string", required=True),
                    "tier": ToolParameter(type="string", required=False),
                },
            ),
            "targets": ToolParameter(
                type="array",
                required=False,
                items=ToolParameter(
                    type="object",
                    properties={
                        "name": ToolParameter(type="string", required=True),
                        "port": ToolParameter(type="integer", required=False),
                    },
                ),
            ),
        },
    )


@pytest.fixture
def strict_tools(monkeypatch):
    monkeypatch.setattr("holmes.core.openai_formatting.STRICT_TOOL_CALLS_ENABLED", True)


@pytest.fixture
def mock_completion():
    with patch("holmes.core.llm.litellm.completion") as mock:
        mock.return_value = _mock_model_response()
        yield mock


def _sent_tools(mock_completion) -> list:
    return mock_completion.call_args.kwargs["tools"]


def _complete(model: str, args: dict, tools: list) -> None:
    _make_llm(model, args).completion(
        messages=[{"role": "user", "content": "hi"}], tools=tools, tool_choice="auto"
    )


@pytest.mark.parametrize("model,args", GROQ_ROUTES)
def test_groq_optional_params_are_not_required(
    mock_completion, strict_tools, model, args
):
    tool = _fetch_pod_logs_tool()
    original = copy.deepcopy(tool)
    # Strict mode lists every parameter as required, optional ones included.
    assert tool["function"]["strict"] is True
    assert len(tool["function"]["parameters"]["required"]) == 7

    _complete(model, args, [tool])

    sent = _sent_tools(mock_completion)[0]["function"]
    assert "strict" not in sent
    parameters = sent["parameters"]
    assert parameters["required"] == ["pod_name", "namespace"]
    # The call Groq rejected in the issue now has every required argument.
    assert set(parameters["required"]) <= ISSUE_ARGUMENTS.keys()
    # Optional parameters still accept null, and unknown keys are still refused.
    assert parameters["properties"]["start_time"] == NULLABLE_STRING
    assert parameters["additionalProperties"] is False
    # Only `required` changes.
    assert parameters["properties"] == original["function"]["parameters"]["properties"]
    # The caller's tool is not mutated.
    assert tool == original


def test_groq_relaxes_nested_objects_and_array_items(mock_completion, strict_tools):
    _complete("groq/openai/gpt-oss-120b", {}, [_nested_tool()])

    parameters = _sent_tools(mock_completion)[0]["function"]["parameters"]
    assert parameters["required"] == ["selector"]

    selector = parameters["properties"]["selector"]
    assert selector["required"] == ["app"]
    assert selector["properties"]["tier"] == NULLABLE_STRING
    assert selector["additionalProperties"] is False

    array_branch, null_branch = parameters["properties"]["targets"]["anyOf"]
    assert null_branch == {"type": "null"}
    assert array_branch["items"]["required"] == ["name"]
    assert array_branch["items"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4.1",
        # The same model through OpenAI or an OpenAI-compatible server.
        "openai/gpt-oss-120b",
        "anthropic/claude-sonnet-4-5",
        "some-unknown-model-xyz",
    ],
)
def test_other_providers_keep_strict_schema(mock_completion, strict_tools, model):
    tool = _fetch_pod_logs_tool()
    _complete(model, {}, [tool])

    sent = _sent_tools(mock_completion)[0]
    assert sent["function"]["strict"] is True
    assert len(sent["function"]["parameters"]["required"]) == 7
    assert sent == tool


def test_relax_keeps_non_nullable_params_and_passes_other_entries_through():
    tool = {
        "type": "function",
        "function": {
            "name": "t",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    # A required union has no null branch, so it stays required.
                    "id": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    "note": {**NULLABLE_STRING, "description": "n"},
                    "mode": {**NULLABLE_STRING, "enum": ["a", "b", None]},
                },
                "required": ["id", "note", "mode"],
                "additionalProperties": False,
            },
        },
    }
    # Built without strict, so `required` already lists only the real ones.
    loose = {
        "type": "function",
        "function": {
            "name": "loose",
            "parameters": {
                "type": "object",
                "properties": {"x": NULLABLE_STRING},
                "required": ["x"],
            },
        },
    }
    original = copy.deepcopy([tool, loose])

    relaxed = _relax_strict_schema_for_groq(
        [tool, "not-a-dict", {"type": "browser_search"}, loose]
    )

    function = relaxed[0]["function"]
    assert "strict" not in function
    assert function["parameters"]["required"] == ["id"]
    assert (
        function["parameters"]["properties"]
        == tool["function"]["parameters"]["properties"]
    )
    assert relaxed[1:3] == ["not-a-dict", {"type": "browser_search"}]
    assert relaxed[3] == loose
    assert [tool, loose] == original
