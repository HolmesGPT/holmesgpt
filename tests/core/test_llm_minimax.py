"""Exercise the documented MiniMax configuration through the real HTTP serializer."""

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
import yaml

from holmes.core.llm import DefaultLLM, LLMModelRegistry


@pytest.fixture
def minimax_models():
    guide = Path(__file__).parents[2] / "docs/ai-providers/other.md"
    example = re.search(r"```yaml\n(.*?)```", guide.read_text(), re.DOTALL)
    assert example is not None
    models = yaml.safe_load(example.group(1))["modelList"]
    assert list(models) == ["minimax-m31-flash", "minimax-m3"]
    return models


@pytest.fixture
def registry_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model_file = tmp_path / "model_list.yaml"
    monkeypatch.setattr("holmes.core.llm.MODEL_LIST_FILE_LOCATION", str(model_file))

    def create(models, selected=None):
        model_file.write_text(yaml.safe_dump(models, sort_keys=False))
        config = SimpleNamespace(
            model=selected,
            api_key=None,
            api_base=None,
            api_version=None,
            should_try_robusta_ai=False,
        )
        return LLMModelRegistry(dal=None, config=config)

    return create


def create_llm(entry):
    params = entry.model_dump(exclude_none=True)
    model = params.pop("model")
    api_key = params.pop("api_key").get_secret_value()
    api_base = params.pop("api_base")
    return DefaultLLM(model=model, api_key=api_key, api_base=api_base, args=params)


@pytest.mark.parametrize(
    "api_base", ["https://api.minimax.io/v1", "https://api.minimaxi.com/v1"]
)
@pytest.mark.parametrize(
    ("selected", "expected_model"),
    [(None, "MiniMax-M3.1-Flash"), ("minimax-m3", "MiniMax-M3")],
)
def test_documented_model_reaches_http(
    registry_factory, minimax_models, api_base, selected, expected_model
):
    for model in minimax_models.values():
        model["api_base"] = api_base
    registry = registry_factory(minimax_models)
    llm = create_llm(registry.get_model_params(selected))
    assert llm.get_context_window_size() == 1_000_000
    messages = [{"role": "user", "content": "Which pods are failing?"}]
    with respx.mock(assert_all_called=True) as router:
        route = router.post(f"{api_base}/chat/completions").respond(
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 1,
                "model": expected_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "None."},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        response = llm.completion(messages=messages, stream=False)
    request = route.calls.last.request
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer minimax-test-key"
    assert body["model"] == expected_model
    assert body["messages"] == messages
    assert "custom_args" not in body
    assert (
        not {"thinking", "reasoning_effort", "reasoning_split", "service_tier"}
        & body.keys()
    )
    assert response.choices[0].message.content == "None."


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_flash_optional_request_parameters(registry_factory, minimax_models, effort):
    minimax_models["minimax-m31-flash"].update(
        reasoning_effort=effort, max_completion_tokens=16384
    )
    llm = create_llm(registry_factory(minimax_models).get_model_params())
    with respx.mock() as router:
        route = router.post("https://api.minimax.io/v1/chat/completions").respond(
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "MiniMax-M3.1-Flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        llm.completion(messages=[{"role": "user", "content": "hello"}])
    body = json.loads(route.calls.last.request.content)
    assert body["reasoning_effort"] == effort
    assert body["max_completion_tokens"] == 16384


@pytest.mark.parametrize(
    "saved_model", ["MiniMax-M2.7", "MiniMax-M2.5", "my-custom-model"]
)
def test_saved_model_selection_is_preserved(
    registry_factory, minimax_models, saved_model
):
    minimax_models["saved-alias"] = {
        "model": f"openai/{saved_model}",
        "api_key": "{{ env.MINIMAX_API_KEY }}",
        "api_base": "https://gateway.example/v1",
    }
    before = copy.deepcopy(minimax_models)
    registry = registry_factory(minimax_models, selected="saved-alias")
    assert registry.get_model_params().model == f"openai/{saved_model}"
    assert registry.get_model_params("minimax-m3").model == "openai/MiniMax-M3"
    llm = create_llm(registry.get_model_params())
    with respx.mock() as router:
        route = router.post("https://gateway.example/v1/chat/completions").respond(
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": saved_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        llm.completion(messages=[{"role": "user", "content": "hello"}])
    assert json.loads(route.calls.last.request.content)["model"] == saved_model
    assert minimax_models == before


def test_flash_tool_history_survives_serialization(registry_factory, minimax_models):
    llm = create_llm(registry_factory(minimax_models).get_model_params())
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_pods", "arguments": "{}"},
    }
    messages = [{"role": "user", "content": "Check the pods"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_pods",
                "description": "List pods",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    with respx.mock() as router:
        route = router.post("https://api.minimax.io/v1/chat/completions").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "id": "test",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "MiniMax-M3.1-Flash",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "reasoning_content": "Need to inspect pods.",
                                    "tool_calls": [tool_call],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "id": "test",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "MiniMax-M3.1-Flash",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "All healthy",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                    },
                ),
            ]
        )
        first = llm.completion(messages=messages, tools=tools, tool_choice="auto")
        messages.append(
            first.choices[0].message.model_dump(
                exclude_defaults=True, exclude_unset=True, exclude_none=True
            )
        )
        messages.append(
            {"role": "tool", "tool_call_id": "call_1", "content": "All healthy"}
        )
        before = copy.deepcopy(messages)
        llm.completion(messages=messages, tools=tools, tool_choice="auto")
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "MiniMax-M3.1-Flash"
    assert body["messages"][1]["reasoning_content"] == "Need to inspect pods."
    assert body["messages"][1]["tool_calls"] == [tool_call]
    assert body["messages"][2]["tool_call_id"] == "call_1"
    assert body["tools"] == tools
    assert messages == before


@pytest.mark.parametrize(
    "api_base", ["https://api.minimax.io/v1", "https://api.minimaxi.com/v1"]
)
@pytest.mark.parametrize("selected", ["minimax-m31-flash", "minimax-m3"])
def test_streaming_preserves_multimodal_messages(
    registry_factory, minimax_models, api_base, selected
):
    minimax_models[selected]["api_base"] = api_base
    llm = create_llm(registry_factory(minimax_models).get_model_params(selected))
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Explain these observations"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/pods.png"},
                },
                {
                    "type": "video_url",
                    "video_url": {"url": "https://example.com/pods.mp4"},
                },
            ],
        }
    ]
    before = copy.deepcopy(messages)
    model = llm.model.removeprefix("openai/")
    chunk = {
        "id": "test-stream",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "Healthy"},
                "finish_reason": "stop",
            }
        ],
    }
    with respx.mock() as router:
        route = router.post(f"{api_base}/chat/completions").respond(
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )
        chunks = list(llm.completion(messages=messages, stream=True))
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == model
    assert body["stream"] is True
    assert body["messages"] == before
    assert messages == before
    assert (
        "".join(chunk.choices[0].delta.content or "" for chunk in chunks) == "Healthy"
    )
