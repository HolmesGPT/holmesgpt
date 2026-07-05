import base64

import pytest

from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tools_utils.tool_result_imaging import (
    SYSTEM_STUB,
    imaging_enabled,
    maybe_image_system_prompt,
    maybe_image_tool_output,
    render_text_to_images,
)


def _dense_text(n_lines: int = 400) -> str:
    return "\n".join(
        f"pod-{i:04d}-abcdef   1/1   Running   {i % 5}   {i}m   10.42.0.{i % 250}   node-{i % 3}"
        for i in range(n_lines)
    )


def test_imaging_disabled_by_default():
    assert imaging_enabled() is False
    assert maybe_image_tool_output(_dense_text()) is None


def test_render_text_to_images_produces_valid_png_pages():
    text = _dense_text()
    rendered = render_text_to_images(text)
    assert rendered is not None
    images, estimated_tokens = rendered
    assert len(images) > 1
    assert estimated_tokens > 0
    for img in images:
        assert img["mimeType"] == "image/png"
        raw = base64.b64decode(img["data"])
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_maybe_image_tool_output_skips_small_results(monkeypatch):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    assert maybe_image_tool_output("short output") is None


def test_maybe_image_tool_output_images_dense_results(monkeypatch):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    images = maybe_image_tool_output(_dense_text())
    assert images is not None
    assert len(images) >= 1


def test_maybe_image_tool_output_skips_unprofitable_sparse_text(monkeypatch):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    # Short lines waste most of each page's width: many rendered pages for few
    # text tokens, so the profitability gate must keep this as plain text.
    sparse = "\n".join(f"line number {i}" for i in range(400))
    assert maybe_image_tool_output(sparse) is None


def test_render_clamps_pathological_font_size(monkeypatch):
    # An absurd font size must not zero out max_cols (infinite wrap loop) or
    # lines_per_page — the invalid value falls back to the default font size.
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING_FONT_SIZE", "100000")
    rendered = render_text_to_images(_dense_text(50))
    assert rendered is not None
    images, _ = rendered
    assert len(images) >= 1


def test_maybe_image_tool_output_respects_max_pages(monkeypatch):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING_MAX_PAGES", "1")
    assert maybe_image_tool_output(_dense_text()) is None


def _system_messages():
    # instruction prose mixed with dense identifiers (tool names, selectors,
    # examples) like the real system prompt — token-dense enough that the
    # profitability gate accepts it
    system = "\n".join(
        f"* `tool_{i}_kubectl_describe--v{i % 9}`: use with `-n app-{i}` and "
        f"selector `app.kubernetes.io/name=svc-{i:03d},rev={i % 7}` "
        f"(e.g. {{\"pod\": \"svc-{i:03d}-7d9f8b{i:04x}\", \"since\": \"{i}m\"}})"
        for i in range(200)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "why is my pod crashing?"},
    ]


def test_system_prompt_imaging_disabled_by_default():
    messages = _system_messages()
    assert maybe_image_system_prompt(messages) is messages


def test_system_prompt_imaging_replaces_system_with_stub(monkeypatch):
    monkeypatch.setenv("HOLMES_SYSTEM_PROMPT_IMAGING", "true")
    messages = _system_messages()
    out = maybe_image_system_prompt(messages)
    assert out is not messages
    assert out[0] == {"role": "system", "content": SYSTEM_STUB}
    assert out[1]["role"] == "user"
    parts = out[1]["content"]
    assert parts[0]["type"] == "text"
    assert any(p["type"] == "image_url" for p in parts[1:])
    # original user question preserved after the injected message
    assert out[2] == messages[1]
    # original list untouched (callers keep text history)
    assert messages[0]["content"].startswith("* `tool_0_")


def test_system_prompt_imaging_skips_small_prompts(monkeypatch):
    monkeypatch.setenv("HOLMES_SYSTEM_PROMPT_IMAGING", "true")
    messages = [
        {"role": "system", "content": "short prompt"},
        {"role": "user", "content": "hi"},
    ]
    assert maybe_image_system_prompt(messages) is messages


def test_system_prompt_imaging_skips_multimodal_system(monkeypatch):
    monkeypatch.setenv("HOLMES_SYSTEM_PROMPT_IMAGING", "true")
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "x" * 20000}]},
        {"role": "user", "content": "hi"},
    ]
    assert maybe_image_system_prompt(messages) is messages


@pytest.fixture
def tool_call_result() -> ToolCallResult:
    return ToolCallResult(
        tool_call_id="call_123",
        tool_name="kubectl_get_pods",
        description="get pods",
        result=StructuredToolResult(
            status=StructuredToolResultStatus.SUCCESS,
            data=_dense_text(),
        ),
    )


def test_to_llm_message_stays_text_when_disabled(tool_call_result):
    message = tool_call_result.to_llm_message()
    assert isinstance(message["content"], str)


def test_to_llm_message_images_large_output(monkeypatch, tool_call_result):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    message = tool_call_result.to_llm_message()
    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call_123"
    content = message["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    # metadata stub survives as text so the LLM can still correlate the call
    assert "kubectl_get_pods" in content[0]["text"]
    assert "PNG image" in content[0]["text"]
    image_parts = [part for part in content[1:] if part["type"] == "image_url"]
    assert len(image_parts) == len(content) - 1
    assert image_parts, "expected at least one rendered page"
    for part in image_parts:
        assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_to_llm_message_imaging_can_be_disabled_per_call(monkeypatch, tool_call_result):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    message = tool_call_result.to_llm_message(enable_imaging=False)
    assert isinstance(message["content"], str)


def test_to_llm_message_never_images_errors(monkeypatch, tool_call_result):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    tool_call_result.result.status = StructuredToolResultStatus.ERROR
    tool_call_result.result.error = "boom"
    message = tool_call_result.to_llm_message()
    assert isinstance(message["content"], str)
