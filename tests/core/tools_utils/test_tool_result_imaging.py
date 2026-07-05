import base64

import pytest

from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.core.tools_utils.tool_result_imaging import (
    imaging_enabled,
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


def test_maybe_image_tool_output_respects_max_pages(monkeypatch):
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING", "true")
    monkeypatch.setenv("HOLMES_TOOL_RESULT_IMAGING_MAX_PAGES", "1")
    assert maybe_image_tool_output(_dense_text()) is None


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
