import json
import os
from pathlib import Path

import pytest

from holmes.core.llm import DefaultLLM
from holmes.core.truncation.compaction import (
    _count_image_tokens_in_messages,
    _strip_images_for_compaction,
    compact_conversation_history,
)

CONVERSATION_HISTORY_FILE_PATH = (
    Path(__file__).parent / "conversation_history_for_compaction.json"
)

_requires_azure = pytest.mark.skipif(
    not all(
        [
            os.environ.get("AZURE_API_BASE"),
            os.environ.get("AZURE_API_VERSION"),
            os.environ.get("AZURE_API_KEY"),
        ]
    ),
    reason="Azure credentials (AZURE_API_BASE, AZURE_API_VERSION, AZURE_API_KEY) are not set",
)


@_requires_azure
def test_conversation_history_compaction_system_prompt_untouched():
    llm = DefaultLLM(model=os.environ.get("model", "azure/gpt-4o"))
    with open(CONVERSATION_HISTORY_FILE_PATH) as file:
        conversation_history = json.load(file)

        system_prompt = {"role": "system", "content": "this is a system prompt"}

        conversation_history.insert(0, system_prompt)

        compaction_result = compact_conversation_history(
            original_conversation_history=conversation_history, llm=llm
        )
        compacted_history = compaction_result.messages_after_compaction
        assert compacted_history
        assert (
            len(compacted_history) == 4
        )  # [0]=system prompt, [1]=last user prompt, [2]=compacted content, [3]=message to continue

        assert compacted_history[0]["role"] == "system"
        assert compacted_history[0]["content"] == system_prompt["content"]

        assert compacted_history[1]["role"] == "user"

        assert compacted_history[2]["role"] == "assistant"

        assert compacted_history[3]["role"] == "user"
        assert "compacted" in compacted_history[3]["content"].lower()


@_requires_azure
def test_conversation_history_compaction():
    llm = DefaultLLM(model=os.environ.get("model", "azure/gpt-4o"))
    with open(CONVERSATION_HISTORY_FILE_PATH) as file:
        conversation_history = json.load(file)

        compaction_result = compact_conversation_history(
            original_conversation_history=conversation_history, llm=llm
        )
        compacted_history = compaction_result.messages_after_compaction
        assert compacted_history
        assert (
            len(compacted_history) == 3
        )  # [0]=last user prompt, [1]=compacted content, [2]=message to continue

        assert compacted_history[0]["role"] == "user"

        assert compacted_history[1]["role"] == "assistant"

        assert compacted_history[2]["role"] == "user"
        assert "compacted" in compacted_history[2]["content"].lower()

        original_tokens = llm.count_tokens(conversation_history)
        compacted_tokens = llm.count_tokens(compacted_history)
        expected_max_compacted_token_count = original_tokens.total_tokens * 0.2
        print(
            f"original_tokens={original_tokens.total_tokens} compacted_tokens={compacted_tokens.total_tokens}"
        )
        print(compacted_history[1]["content"])
        assert compacted_tokens.total_tokens < expected_max_compacted_token_count


# --- Regression test: post-compaction message shape (no LLM required) ---
#
# claude-opus-4.6 and newer reject any request whose conversation ends on an
# assistant turn: "This model does not support assistant message prefill"
# (HTTP 400). The post-compaction history used to end with
# [..., assistant summary, system marker]; Anthropic folds trailing system
# messages, so the request effectively ended on the assistant summary and
# every compaction-triggering investigation on opus-4.6 crashed mid-loop.
# The continuation marker must therefore be a user message.


def _fake_llm_for_compaction(summary_text="Summary of the investigation so far."):
    """Minimal LLM stub for compact_conversation_history (no network)."""

    class FakeUsage:
        total_tokens = 100

    class FakeMessage:
        def model_dump(self, **kwargs):
            return {"role": "assistant", "content": summary_text}

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]
        usage = None  # RequestStats.from_response handles missing usage

    class FakeLLM:
        def count_tokens(self, messages):
            return FakeUsage()

        def get_context_window_size(self):
            return 128000

        def get_maximum_output_token(self):
            return 4096

        def completion(self, messages, **kwargs):
            return FakeResponse()

    return FakeLLM()


def test_compacted_history_must_end_with_user_message():
    """The next LLM call after compaction must not end on an assistant turn."""
    conversation_history = [
        {"role": "system", "content": "You are an investigation agent."},
        {"role": "user", "content": "Why is my payment service failing?"},
        {"role": "assistant", "content": "Let me check the pods."},
        {"role": "assistant", "content": "I found several restarts, digging into logs."},
    ]

    result = compact_conversation_history(
        original_conversation_history=conversation_history,
        llm=_fake_llm_for_compaction(),  # type: ignore[arg-type]
    )
    messages = result.messages_after_compaction

    # Shape: [system prompt, last user prompt, assistant summary, continuation marker]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "compacted" in messages[-1]["content"].lower()

    # The critical invariant: the conversation must NOT end on the assistant
    # summary (directly, or via a trailing system message that providers fold),
    # otherwise opus >= 4.6 rejects the next request as assistant prefill.
    assert messages[-1]["role"] == "user"


def test_compacted_history_without_system_prompt_ends_with_user_message():
    conversation_history = [
        {"role": "user", "content": "Investigate the checkout latency."},
        {"role": "assistant", "content": "Checking metrics now."},
    ]

    result = compact_conversation_history(
        original_conversation_history=conversation_history,
        llm=_fake_llm_for_compaction(),  # type: ignore[arg-type]
    )
    messages = result.messages_after_compaction

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[-1]["role"] == "user"


# --- Unit tests for _strip_images_for_compaction (no LLM required) ---


def test_strip_images_for_compaction_no_images():
    """Messages without images pass through unchanged."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "content": "some text result"},
    ]
    result = _strip_images_for_compaction(messages)
    assert result == messages


def test_strip_images_for_compaction_replaces_image_blocks():
    """Image blocks are replaced with a placeholder text block."""
    messages = [
        {
            "role": "tool",
            "content": [
                {"type": "text", "text": "Rendered panel screenshot."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
            ],
            "token_count": 500,
        }
    ]
    result = _strip_images_for_compaction(messages)
    assert len(result) == 1
    content = result[0]["content"]
    # Text block preserved
    assert content[0]["type"] == "text"
    assert "Rendered panel screenshot." in content[0]["text"]
    # Image blocks replaced with placeholder
    assert content[1]["type"] == "text"
    assert "2 image(s)" in content[1]["text"]
    assert "stripped" in content[1]["text"]
    # No image_url blocks remain
    assert not any(b.get("type") == "image_url" for b in content)
    # Token count cache must be invalidated
    assert "token_count" not in result[0]


def test_strip_images_for_compaction_preserves_non_image_messages():
    """Non-multimodal messages are preserved alongside stripped ones."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Render the dashboard"},
        {
            "role": "tool",
            "content": [
                {"type": "text", "text": "Dashboard screenshot"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,CCC"}},
            ],
        },
        {"role": "assistant", "content": "I see a spike in the CPU panel."},
    ]
    result = _strip_images_for_compaction(messages)
    assert len(result) == 4
    assert result[0]["content"] == "You are helpful."
    assert result[1]["content"] == "Render the dashboard"
    # Tool message had images stripped
    assert result[2]["content"][0]["text"] == "Dashboard screenshot"
    assert "1 image(s)" in result[2]["content"][1]["text"]
    assert "stripped" in result[2]["content"][1]["text"]
    assert result[3]["content"] == "I see a spike in the CPU panel."


def test_strip_images_with_disk_paths_in_text():
    """When text mentions saved image paths, the text is preserved and images stripped."""
    messages = [
        {
            "role": "tool",
            "content": [
                {
                    "type": "text",
                    "text": "Images saved to disk:\n  - /tmp/results/grafana_render_abc_img0.png\n",
                },
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]
    result = _strip_images_for_compaction(messages)
    # Text block with disk paths is preserved
    assert result[0]["content"][0]["text"].startswith("Images saved to disk")
    # Image block is stripped and placeholder added
    placeholder = result[0]["content"][-1]["text"]
    assert "1 image(s)" in placeholder
    assert "stripped" in placeholder


def test_count_image_tokens_no_images():
    """Messages without images return 0 tokens."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "content": "text only"},
    ]

    class FakeLLM:
        def count_tokens(self, messages):
            class Usage:
                total_tokens = 0
            return Usage()

    assert _count_image_tokens_in_messages(messages, FakeLLM()) == 0  # type: ignore


def test_count_image_tokens_with_images():
    """Image blocks are counted via the LLM token counter."""
    messages = [
        {
            "role": "tool",
            "content": [
                {"type": "text", "text": "some text"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]

    class FakeLLM:
        def count_tokens(self, messages):
            # Should receive a synthetic message with only image blocks
            class Usage:
                total_tokens = 1600
            return Usage()

    assert _count_image_tokens_in_messages(messages, FakeLLM()) == 1600  # type: ignore
