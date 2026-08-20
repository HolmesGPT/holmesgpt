import json
import os
from pathlib import Path

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from holmes.core.llm import DefaultLLM
from holmes.core.truncation.compaction import (
    COMPACTION_TRUNCATION_NOTICE,
    _count_image_tokens_in_messages,
    _flatten_tool_messages_for_compaction,
    _halve_message_content,
    _strip_images_for_compaction,
    _truncate_messages_for_compaction,
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
    """Live compaction keeps the system prompt as the first message."""
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
            len(compacted_history) == 3
        )  # [0]=system prompt, [1]=summary (user), [2]=last user prompt

        assert compacted_history[0]["role"] == "system"
        assert compacted_history[0]["content"] == system_prompt["content"]

        assert compacted_history[1]["role"] == "user"
        assert "compacted" in compacted_history[1]["content"].lower()

        assert compacted_history[2]["role"] == "user"


@_requires_azure
def test_conversation_history_compaction():
    """Live compaction produces a [user summary, last user prompt] history."""
    llm = DefaultLLM(model=os.environ.get("model", "azure/gpt-4o"))
    with open(CONVERSATION_HISTORY_FILE_PATH) as file:
        conversation_history = json.load(file)

        compaction_result = compact_conversation_history(
            original_conversation_history=conversation_history, llm=llm
        )
        compacted_history = compaction_result.messages_after_compaction
        assert compacted_history
        assert (
            len(compacted_history) == 2
        )  # [0]=summary (user), [1]=last user prompt

        assert compacted_history[0]["role"] == "user"
        assert "compacted" in compacted_history[0]["content"].lower()

        assert compacted_history[1]["role"] == "user"

        original_tokens = llm.count_tokens(conversation_history)
        compacted_tokens = llm.count_tokens(compacted_history)
        expected_max_compacted_token_count = original_tokens.total_tokens * 0.2
        print(
            f"original_tokens={original_tokens.total_tokens} compacted_tokens={compacted_tokens.total_tokens}"
        )
        print(compacted_history[0]["content"])
        assert compacted_tokens.total_tokens < expected_max_compacted_token_count


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
            """Return a fixed token usage for any input."""
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
            """Return a fixed token usage for any input."""
            # Should receive a synthetic message with only image blocks
            class Usage:
                total_tokens = 1600
            return Usage()

    assert _count_image_tokens_in_messages(messages, FakeLLM()) == 1600  # type: ignore


# --- Unit tests for _flatten_tool_messages_for_compaction (no LLM required) ---
# Regression for ROB-424: the compaction summary call sends no `tools`, so any
# tool_use/tool_result blocks left in the history make gateways translating to
# Bedrock Converse fail with "The toolConfig field must be defined ...".


def test_flatten_tool_messages_removes_tool_calls_and_tool_role():
    """After flattening there must be no tool_calls and no role=='tool' messages."""
    messages = [
        {"role": "user", "content": "why are pods slow?"},
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "kubectl_get",
                        "arguments": '{"resource": "pods", "namespace": "app"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "node-3 MemoryPressure=True"},
    ]
    result = _flatten_tool_messages_for_compaction(messages)

    assert all("tool_calls" not in m for m in result)
    assert all(m.get("role") != "tool" for m in result)
    # roles a Converse gateway accepts without a toolConfig
    assert {m["role"] for m in result} <= {"system", "user", "assistant"}


def test_flatten_tool_messages_preserves_name_args_and_result_as_text():
    """The prompt's "Tool Calls" section needs name + full args + outcome as text."""
    messages = [
        {
            "role": "assistant",
            "content": "Checking.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "kubectl_get",
                        "arguments": '{"resource": "pods"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "pod oomkilled"},
    ]
    result = _flatten_tool_messages_for_compaction(messages)

    assistant_text = result[0]["content"]
    assert "Checking." in assistant_text
    assert "kubectl_get" in assistant_text
    assert '{"resource": "pods"}' in assistant_text  # full arguments preserved

    tool_text = result[1]["content"]
    assert result[1]["role"] == "user"
    assert "pod oomkilled" in tool_text  # tool result content preserved


def test_flatten_tool_messages_preserves_image_blocks():
    """Image blocks in tool results survive flattening (image logic runs after)."""
    messages = [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": [
                {"type": "text", "text": "screenshot"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]
    result = _flatten_tool_messages_for_compaction(messages)
    assert result[0]["role"] == "user"
    content = result[0]["content"]
    assert any(b.get("type") == "image_url" for b in content)


def test_flatten_tool_messages_passes_through_plain_messages():
    """Messages without tool blocks are returned unchanged (same objects)."""
    messages = [
        {"role": "system", "content": "You are HolmesGPT."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result = _flatten_tool_messages_for_compaction(messages)
    assert result == messages


# --- Unit tests for the summarization call shape and fallback (no network) ---


class _Usage:
    """Minimal token-usage stub for the fake LLM."""
    total_tokens = 100


class RecordingFakeLLM:
    """Fake LLM that records completion calls and replays canned responses."""

    def __init__(self, responses):
        """Store canned responses to replay, newest first."""
        self.responses = list(responses)
        self.calls: list[dict] = []

    def completion(self, messages, tools=None, tool_choice=None, **kwargs):
        """Record the call and replay the next canned response (or raise it)."""
        self.calls.append(
            {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        )
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def count_tokens(self, messages, tools=None):
        """Return a fixed token usage for any input."""
        return _Usage()

    def get_context_window_size(self):
        """Return a fixed context window size."""
        return 100000

    def get_maximum_output_token(self):
        """Return a fixed maximum output token count."""
        return 4096


def _make_response(content=None, tool_calls=None, **message_kwargs):
    """Build a minimal litellm ModelResponse wrapping one assistant message."""
    message = Message(
        content=content, role="assistant", tool_calls=tool_calls, **message_kwargs
    )
    return ModelResponse(choices=[Choices(message=message)])


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kubectl_get",
            "description": "run kubectl get",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _history_with_tool_calls():
    """A small agentic history containing a tool call and its result."""
    return [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "why is my pod crashing?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "kubectl_get", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "kubectl_get", "content": "CrashLoopBackOff"},
    ]


def test_compaction_primary_call_keeps_native_history_and_attaches_tools():
    """The primary summarization call keeps native history and attaches tools."""
    llm = RecordingFakeLLM([_make_response(content="THE SUMMARY")])
    result = compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(),
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )

    assert len(llm.calls) == 1
    call = llm.calls[0]
    # Tools attached so Converse-translating gateways get toolConfig (ROB-424)
    assert call["tools"] == _TOOLS
    assert call["tool_choice"] == "auto"
    # Native history preserved: system kept, tool message not flattened
    roles = [m["role"] for m in call["messages"]]
    assert roles[0] == "system"
    assert "tool" in roles
    # Instructions appended as the final user message
    assert call["messages"][-1]["role"] == "user"

    assert result.summary == "THE SUMMARY"


def test_compaction_output_shape_user_summary_no_trailing_system():
    """The compacted history is [system, user summary, last user prompt]."""
    llm = RecordingFakeLLM([_make_response(content="THE SUMMARY")])
    result = compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(),
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )
    compacted = result.messages_after_compaction

    # [system, user summary, last user prompt] — no assistant message, and no
    # system message anywhere but index 0 (ROB-425 / ROB-665)
    assert [m["role"] for m in compacted] == ["system", "user", "user"]
    assert compacted[0]["content"] == "sys prompt"
    assert "THE SUMMARY" in compacted[1]["content"]
    assert "compacted" in compacted[1]["content"].lower()
    assert compacted[2]["content"] == "why is my pod crashing?"


def test_compaction_falls_back_when_model_calls_a_tool():
    """A tool-call response triggers the flattened, tool-less retry."""
    tool_call_response = _make_response(
        tool_calls=[
            {
                "id": "c9",
                "type": "function",
                "function": {"name": "kubectl_get", "arguments": "{}"},
            }
        ]
    )
    llm = RecordingFakeLLM([tool_call_response, _make_response(content="FALLBACK SUMMARY")])
    result = compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(),
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )

    assert len(llm.calls) == 2
    fallback_call = llm.calls[1]
    # Fallback sends no tools and a flattened, system-less history
    assert fallback_call["tools"] is None
    roles = [m["role"] for m in fallback_call["messages"]]
    assert "system" not in roles
    assert "tool" not in roles
    assert not any(m.get("tool_calls") for m in fallback_call["messages"])

    assert result.summary == "FALLBACK SUMMARY"
    assert result.fallback_used is True
    assert result.fallback_reason is not None
    assert "tool call" in result.fallback_reason


def test_compaction_falls_back_when_primary_request_fails():
    """A failing primary request triggers the flattened, tool-less retry."""
    llm = RecordingFakeLLM(
        [RuntimeError("400 toolConfig must be defined"), _make_response(content="FALLBACK SUMMARY")]
    )
    result = compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(),
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )
    assert len(llm.calls) == 2
    assert result.summary == "FALLBACK SUMMARY"
    assert result.fallback_used is True
    assert result.fallback_reason is not None
    assert "400 toolConfig must be defined" in result.fallback_reason


def test_compaction_returns_original_history_when_fallback_also_fails():
    """If the flattened retry also fails, compaction degrades gracefully:
    the original history is returned unchanged instead of raising."""
    history = _history_with_tool_calls()
    llm = RecordingFakeLLM(
        [
            RuntimeError("400 toolConfig must be defined"),
            RuntimeError("502 bad gateway"),
        ]
    )
    result = compact_conversation_history(
        original_conversation_history=history,
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )
    assert len(llm.calls) == 2
    assert result.messages_after_compaction == history
    assert result.fallback_used is True
    assert result.fallback_reason is not None
    assert "400 toolConfig must be defined" in result.fallback_reason
    assert "502 bad gateway" in result.fallback_reason


def test_compaction_primary_success_reports_no_fallback():
    """A successful primary summarization reports fallback_used=False."""
    llm = RecordingFakeLLM([_make_response(content="THE SUMMARY")])
    result = compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(),
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )
    assert result.fallback_used is False
    assert result.fallback_reason is None


def test_compaction_summary_never_stores_thinking_blocks():
    """Thinking blocks from the summarization response never enter history."""
    response = _make_response(
        content="THE SUMMARY",
        reasoning_content="thinking about it...",
        thinking_blocks=[
            {"type": "thinking", "thinking": "thinking about it...", "signature": "SIG=="}
        ],
    )
    llm = RecordingFakeLLM([response])
    result = compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(),
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )
    summary_message = result.messages_after_compaction[1]
    assert summary_message["role"] == "user"
    assert isinstance(summary_message["content"], str)
    assert "thinking_blocks" not in summary_message
    assert "reasoning_content" not in summary_message
    assert "SIG==" not in json.dumps(result.messages_after_compaction)


# --- Unit tests for compaction-input truncation (no network) ---
# Regression for the "prompt is too long" failure: when the history overshoots
# the model's context window (e.g. several large parallel tool results in one
# iteration), the summarization request itself exceeded the window and
# compaction was guaranteed to fail, killing the turn.


class TokenCountingFakeLLM(RecordingFakeLLM):
    """Fake LLM that counts ~1 token per 4 chars and caches like DefaultLLM."""

    def __init__(self, responses, context_window=2000, max_output=100):
        """Store canned responses plus a configurable context window."""
        super().__init__(responses)
        self.context_window = context_window
        self.max_output = max_output

    def count_tokens(self, messages, tools=None):
        """Sum len(text)//4 per message, caching under "token_count"."""
        total = 0
        for message in messages:
            cached = message.get("token_count")
            if cached is None:
                cached = _message_text_length(message) // 4
                message["token_count"] = cached
            total += cached

        class Usage:
            total_tokens = total

        return Usage()

    def get_context_window_size(self):
        """Return the configured context window size."""
        return self.context_window

    def get_maximum_output_token(self):
        """Return the configured maximum output token count."""
        return self.max_output


def _message_text_length(message):
    """Total text length of a message's content (str or block list)."""
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return 0


def test_halve_message_content_string():
    message = {"role": "tool", "content": "x" * 2000, "token_count": 500}
    assert _halve_message_content(message) is True
    assert message["content"].startswith("x" * 1000)
    assert COMPACTION_TRUNCATION_NOTICE in message["content"]
    assert "token_count" not in message


def test_halve_message_content_block_list():
    message = {
        "role": "tool",
        "content": [
            {"type": "text", "text": "y" * 2000},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }
    assert _halve_message_content(message) is True
    assert message["content"][0]["text"].startswith("y" * 1000)
    assert COMPACTION_TRUNCATION_NOTICE in message["content"][0]["text"]
    assert message["content"][1]["type"] == "image_url"


def test_halve_message_content_too_small_to_shrink():
    message = {"role": "user", "content": "short"}
    assert _halve_message_content(message) is False
    assert message["content"] == "short"


def test_truncate_messages_fits_budget_without_mutating_originals():
    llm = TokenCountingFakeLLM([])
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "question"},
        {"role": "tool", "tool_call_id": "c1", "content": "A" * 40000},
        {"role": "tool", "tool_call_id": "c2", "content": "B" * 40000},
    ]
    truncated = _truncate_messages_for_compaction(messages, llm, None, input_budget=5000)

    assert llm.count_tokens(messages=truncated).total_tokens <= 5000
    # Originals untouched
    assert messages[2]["content"] == "A" * 40000
    assert messages[3]["content"] == "B" * 40000
    # Small messages untouched, big ones carry the notice
    assert truncated[0]["content"] == "sys"
    assert truncated[1]["content"] == "question"
    assert COMPACTION_TRUNCATION_NOTICE in truncated[2]["content"]
    assert COMPACTION_TRUNCATION_NOTICE in truncated[3]["content"]


def test_truncate_messages_stops_when_nothing_left_to_shrink():
    llm = TokenCountingFakeLLM([])
    messages = [{"role": "user", "content": "tiny"}]
    truncated = _truncate_messages_for_compaction(messages, llm, None, input_budget=0)
    assert truncated[0]["content"] == "tiny"


def test_compaction_truncates_oversized_history_before_summarization():
    """A history larger than the context window is truncated so the
    summarization request fits, instead of failing with "prompt is too long"."""
    llm = TokenCountingFakeLLM(
        [_make_response(content="THE SUMMARY")], context_window=2000, max_output=100
    )
    history = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "why is my pod crashing?"},
        {"role": "tool", "tool_call_id": "c1", "content": "A" * 20000},  # ~5000 tokens
        {"role": "tool", "tool_call_id": "c2", "content": "B" * 20000},  # ~5000 tokens
    ]
    original = json.loads(json.dumps(history))

    result = compact_conversation_history(
        original_conversation_history=history, llm=llm  # type: ignore
    )

    assert result.summary == "THE SUMMARY"
    summarization_messages = llm.calls[0]["messages"]
    sent_tokens = llm.count_tokens(messages=list(summarization_messages)).total_tokens
    assert sent_tokens + llm.max_output <= llm.context_window
    assert any(
        COMPACTION_TRUNCATION_NOTICE in str(m.get("content"))
        for m in summarization_messages
    )
    # Original history not mutated by truncation (token_count cache aside)
    for msg, orig in zip(history, original):
        assert msg["content"] == orig["content"]


def test_compaction_small_history_is_not_truncated():
    llm = TokenCountingFakeLLM(
        [_make_response(content="THE SUMMARY")], context_window=100000
    )
    result = compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(), llm=llm  # type: ignore
    )
    assert result.summary == "THE SUMMARY"
    assert not any(
        COMPACTION_TRUNCATION_NOTICE in str(m.get("content"))
        for m in llm.calls[0]["messages"]
    )


def test_compaction_returns_original_history_when_both_attempts_unusable():
    """When both attempts yield no text the original history is returned."""
    llm = RecordingFakeLLM([_make_response(content=""), _make_response(content="")])
    history = _history_with_tool_calls()
    result = compact_conversation_history(
        original_conversation_history=history,
        llm=llm,  # type: ignore
        tools=_TOOLS,
    )
    assert len(llm.calls) == 2
    assert result.summary is None
    assert result.messages_after_compaction == history
