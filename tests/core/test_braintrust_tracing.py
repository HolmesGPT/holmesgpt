"""Braintrust must never log a boolean as a span metric.

OpenRouter returns ``usage.is_byok: false``. The Braintrust API rejects any
record whose ``metrics`` hold a boolean, and the SDK then drops the whole
upload batch, losing unrelated spans with it (ROB-1713). These tests run the
SDK's own usage parsing on such a response and capture what it would upload
in an in-memory logger, so nothing reaches the network.
"""

import braintrust
import httpx
import openai
import pytest
from braintrust import logger as bt_logger
from braintrust.oai import wrap_openai
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from holmes.core.tracing import BraintrustTracer

OPENROUTER_USAGE = {
    "prompt_tokens": 11,
    "completion_tokens": 7,
    "total_tokens": 18,
    "cost": 0.0021,
    "is_byok": False,
    "prompt_tokens_details": {"cached_tokens": 3, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 2},
}


@pytest.fixture
def memory_logger(monkeypatch):
    """A Braintrust logger that keeps records in memory instead of uploading them."""

    def fake_logger_metadata(*args, **kwargs):
        project = bt_logger.ObjectMetadata(id="holmes-test", name="holmes-test", full_info={})
        return bt_logger.OrgProjectMetadata(org_id="test-org", project=project)

    monkeypatch.setattr(bt_logger, "_compute_logger_metadata", fake_logger_metadata)
    bt_logger._internal_reset_global_state()
    bt_logger.login(api_key=bt_logger.TEST_API_KEY)  # the SDK's offline login
    with bt_logger._internal_with_memory_background_logger() as bgl:
        braintrust.init_logger(project="holmes-test")
        yield bgl
    bt_logger._internal_reset_global_state()


def _llm_span_metrics(memory_logger) -> dict:
    spans = memory_logger.pop()
    llm_spans = [s for s in spans if s.get("span_attributes", {}).get("type") == "llm"]
    assert len(llm_spans) == 1, spans
    return llm_spans[0]["metrics"]


def _assert_numeric_metrics(metrics: dict) -> None:
    bools = {k: v for k, v in metrics.items() if isinstance(v, bool)}
    assert not bools, f"boolean metrics would be rejected by the Braintrust API: {bools}"
    assert all(isinstance(v, (int, float)) for v in metrics.values()), metrics


def test_wrapped_litellm_drops_boolean_usage_fields(memory_logger, monkeypatch):
    monkeypatch.setenv("BRAINTRUST_API_KEY", "test-key")

    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            return ModelResponse(
                id="gen-1",
                model=kwargs["model"],
                choices=[
                    Choices(
                        index=0,
                        finish_reason="stop",
                        message=Message(role="assistant", content="pod is OOMKilled"),
                    )
                ],
                usage=Usage(**OPENROUTER_USAGE),
            )

    llm = BraintrustTracer(project="holmes-test").wrap_llm(FakeLiteLLM)
    response = llm.completion(
        model="openrouter/deepseek/deepseek-v4-pro",
        messages=[{"role": "user", "content": "why is my pod crashing?"}],
    )

    assert response.choices[0].message.content == "pod is OOMKilled"
    metrics = _llm_span_metrics(memory_logger)
    _assert_numeric_metrics(metrics)
    assert "is_byok" not in metrics
    assert metrics["prompt_tokens"] == 11
    assert metrics["completion_tokens"] == 7


def test_wrapped_openai_client_drops_boolean_usage_fields(memory_logger):
    """The eval classifier calls OpenRouter through ``wrap_openai``."""

    def openrouter(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "gen-2",
                "object": "chat.completion",
                "created": 0,
                "model": "openai/gpt-4.1",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Y"},
                    }
                ],
                "usage": OPENROUTER_USAGE,
            },
        )

    client = wrap_openai(
        openai.OpenAI(
            api_key="test-key",
            base_url="https://openrouter.test/api/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(openrouter)),
        )
    )
    client.chat.completions.create(
        model="openai/gpt-4.1",
        messages=[{"role": "user", "content": "grade this"}],
    )

    metrics = _llm_span_metrics(memory_logger)
    _assert_numeric_metrics(metrics)
    assert "is_byok" not in metrics
    assert metrics["prompt_tokens"] == 11
