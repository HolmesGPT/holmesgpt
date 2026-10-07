import json
import os
from pathlib import Path

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from holmes.core.llm import ContextWindowUsage, DefaultLLM
from holmes.core.truncation import compaction
from holmes.plugins.prompts import load_and_render_prompt
from holmes.core.truncation.compaction import (
    COMPACTION_TRUNCATION_NOTE,
    TRUNCATED_INPUT_INSTRUCTION,
    SHORT_TRUNCATION_MARKER,
    TRUNCATION_MARKER,
    _count_image_tokens_in_messages,
    _fit_history_to_token_budget,
    _is_context_length_error,
    _flatten_tool_messages_for_compaction,
    _strip_images_for_compaction,
    _truncate_text,
    _water_fill_cap,
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


# --- Fitting the summarization request into the context window (ROB-1519) ---


class CharCountingFakeLLM(RecordingFakeLLM):
    """Fake LLM whose token count is proportional to message text, and which
    rejects requests larger than ``accept_up_to`` tokens like a real provider."""

    def __init__(self, responses, context_window=10_000, max_output=2_000, accept_up_to=None):
        """Configure the window, output reserve and the provider-side size limit."""
        super().__init__(responses)
        self.context_window = context_window
        self.max_output = max_output
        self.accept_up_to = accept_up_to

    def count_tokens(self, messages, tools=None):
        """Roughly four characters per token, plus a fixed cost for tool definitions."""
        total = sum(len(json.dumps(m.get("content"))) for m in messages) // 4 + (100 if tools else 0)
        return ContextWindowUsage(
            total_tokens=total,
            tools_tokens=0,
            system_tokens=0,
            user_tokens=0,
            tools_to_call_tokens=0,
            assistant_tokens=0,
            other_tokens=0,
        )

    def completion(self, messages, tools=None, tool_choice=None, **kwargs):
        """Reject requests over ``accept_up_to`` tokens, else replay a canned response."""
        if self.accept_up_to is not None and self.count_tokens(messages, tools).total_tokens > self.accept_up_to:
            self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
            raise RuntimeError("400 The conversation is too long for the AI model's context window.")
        return super().completion(messages, tools=tools, tool_choice=tool_choice, **kwargs)

    def get_context_window_size(self):
        """Return the configured context window."""
        return self.context_window

    def get_maximum_output_token(self):
        """Return the configured output reserve."""
        return self.max_output


def _oversized_history(tool_result_chars=(30_000, 12_000, 400)):
    """A history whose tool results overflow a 10k-token window."""
    history = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "why is checkout slow? code ZEBRA-4417"},
    ]
    for i, size in enumerate(tool_result_chars):
        history.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": {"name": "kubectl_get", "arguments": "{}"}}
                ],
            }
        )
        history.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "name": "kubectl_get",
                "content": f"HEAD-{i} " + "x" * size + f" TAIL-{i}",
                "token_count": size // 4,
            }
        )
    history.append({"role": "user", "content": "what did you find?"})
    return history


def _request_tokens(llm, call):
    """Tokens the fake provider counted for a recorded request."""
    return llm.count_tokens(call["messages"], call["tools"]).total_tokens


def test_compaction_truncates_oversized_history_to_fit_window():
    """A history larger than the window is cut down before it is sent, so the
    summarization request is accepted instead of failing on every attempt."""
    llm = CharCountingFakeLLM([_make_response(content="THE SUMMARY")], accept_up_to=10_000 - 2_000)
    history = _oversized_history()
    assert llm.count_tokens(history).total_tokens > llm.context_window

    result = compact_conversation_history(original_conversation_history=history, llm=llm, tools=_TOOLS)  # type: ignore

    assert len(llm.calls) == 1
    assert result.summary == "THE SUMMARY"
    assert result.input_truncated is True
    assert result.fallback_used is False
    assert _request_tokens(llm, llm.calls[0]) <= llm.context_window - llm.max_output
    sent = llm.calls[0]["messages"]
    # Native shape and system prompt preserved; only the longest results are cut,
    # keeping both ends of each.
    assert sent[0] == history[0]
    assert [m["role"] for m in sent[:-1]] == [m["role"] for m in history]
    truncated_results = [m["content"] for m in sent if m["role"] == "tool"]
    assert "HEAD-0" in truncated_results[0] and "TAIL-0" in truncated_results[0]
    assert "truncated to fit the compaction request" in truncated_results[0]
    # The agent is told the summary is partial, so it re-queries instead of
    # concluding that the cut content does not exist.
    assert COMPACTION_TRUNCATION_NOTE in result.messages_after_compaction[1]["content"]
    # ...and the summarizer is told which outputs it only partially saw.
    assert TRUNCATED_INPUT_INSTRUCTION in sent[-1]["content"]
    assert truncated_results[2] == history[-2]["content"]
    # A stale token-count cache must not survive truncation.
    assert all("token_count" not in m for m in sent if "truncated to fit" in str(m.get("content")))
    # The caller's history is never mutated.
    assert history == _oversized_history()


def test_compaction_does_not_truncate_history_that_fits():
    """A history within budget is sent untouched (keeps the prompt-cache prefix)."""
    llm = CharCountingFakeLLM([_make_response(content="THE SUMMARY")], context_window=100_000)
    history = _oversized_history()
    result = compact_conversation_history(original_conversation_history=history, llm=llm, tools=_TOOLS)  # type: ignore

    assert result.input_truncated is False
    assert llm.calls[0]["messages"][:-1] == history
    assert COMPACTION_TRUNCATION_NOTE not in result.messages_after_compaction[1]["content"]
    assert TRUNCATED_INPUT_INSTRUCTION not in llm.calls[0]["messages"][-1]["content"]


def test_compaction_fallback_shrinks_budget_when_provider_counts_more_tokens():
    """When the provider still rejects the request (its tokenizer counts more than
    ours), each fallback halves the budget until a request is accepted."""
    llm = CharCountingFakeLLM(
        [_make_response(content="FALLBACK SUMMARY")], accept_up_to=2_500
    )
    result = compact_conversation_history(
        original_conversation_history=_oversized_history(), llm=llm, tools=_TOOLS  # type: ignore
    )

    assert len(llm.calls) == 3
    sizes = [_request_tokens(llm, c) for c in llm.calls]
    assert sizes[0] > sizes[1] > sizes[2]
    assert sizes[2] <= 2_500
    assert result.summary == "FALLBACK SUMMARY"
    assert result.fallback_used is True
    assert result.input_truncated is True
    assert "too long" in (result.fallback_reason or "")


def test_compaction_gives_up_after_all_fallback_budgets_fail():
    """If even the smallest budget is rejected, the original history is returned."""
    llm = CharCountingFakeLLM([], accept_up_to=10)
    history = _oversized_history()
    result = compact_conversation_history(original_conversation_history=history, llm=llm, tools=_TOOLS)  # type: ignore

    assert len(llm.calls) == 1 + 2
    assert result.summary is None
    assert result.messages_after_compaction == history
    assert result.fallback_reason is not None
    assert "budget 25%" in result.fallback_reason


def test_compaction_does_not_repeat_identical_fallback_request():
    """A fallback that already fits does not resend the same request at a smaller budget."""
    llm = RecordingFakeLLM([RuntimeError("400 toolConfig"), RuntimeError("502"), _make_response(content="unused")])
    compact_conversation_history(
        original_conversation_history=_history_with_tool_calls(), llm=llm, tools=_TOOLS  # type: ignore
    )
    assert len(llm.calls) == 2


def test_fit_history_handles_block_content_and_keeps_images():
    """The longest text blocks are cut first; short blocks and images are kept."""
    llm = CharCountingFakeLLM([])
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    messages = [
        {"role": "system", "content": "s" * 20_000},
        {"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "a" * 16_000}, image, {"type": "text", "text": "b" * 8_000}]},
        {"role": "assistant", "content": None},
    ]
    fitted, truncated, fits = _fit_history_to_token_budget(messages, llm, None, 6_000)

    assert truncated is True
    assert fits is True
    assert fitted[0] == messages[0]
    blocks = fitted[1]["content"]
    assert blocks[1] == image
    assert len(blocks[0]["text"]) < 16_000
    assert llm.count_tokens(fitted).total_tokens <= 6_000
    assert fitted[2] == messages[2]

    short = {"type": "text", "text": "[1 image(s) were present but stripped from compaction]"}
    message = {"role": "tool", "content": [{"type": "text", "text": "a" * 20_000}, short]}
    fitted, _, _ = _fit_history_to_token_budget([message], llm, None, 1_000)
    assert fitted[0]["content"][1] == short


def test_fit_history_best_effort_when_only_untruncatable_content():
    """With nothing truncatable the history is returned as-is (best effort)."""
    llm = CharCountingFakeLLM([])
    messages = [{"role": "system", "content": "s" * 40_000}]
    fitted, truncated, fits = _fit_history_to_token_budget(messages, llm, None, 100)
    assert fitted == messages
    assert truncated is True
    assert fits is False


@pytest.mark.parametrize(
    "lengths,to_remove,expected_cap",
    [
        ([100, 10], 50, 50),
        ([100, 60], 60, 50),
        ([100, 100], 1, 99),
        ([10, 10], 100, 0),
        ([], 5, 0),
    ],
)
def test_water_fill_cap(lengths, to_remove, expected_cap):
    """The cap trims only the longest messages, removing at least the requested amount."""
    cap = _water_fill_cap(lengths, to_remove)
    assert cap == expected_cap
    if sum(lengths) >= to_remove:
        assert sum(max(0, n - cap) for n in lengths) >= to_remove


def test_truncate_text_keeps_head_and_tail():
    """Short text is untouched; long text keeps both ends around a marker."""
    assert _truncate_text("short", 10) == "short"
    text = "HEAD" + "x" * 1_000 + "TAIL"
    out = _truncate_text(text, 300)
    assert out.startswith("HEAD") and out.endswith("TAIL")
    assert len(out) <= 300
    removed = int(out.split("[... ")[1].split(" characters")[0])
    assert len(out) - len(out.split("\n[...")[0]) - len(out.split("...]\n")[1]) > 0
    assert removed == len(text) - (len(out) - len(TRUNCATION_MARKER.format(removed=removed)))
    # Trimming a few characters must shrink the text, not grow it with the marker.
    assert len(_truncate_text(text, len(text) - 3)) <= len(text) - 3
    # Below the full marker's length the short marker is used, even below its own length.
    assert _truncate_text("abcdef" * 10, 5) == "a" + SHORT_TRUNCATION_MARKER + "f"
    assert _truncate_text("abcdef" * 10, 2) == SHORT_TRUNCATION_MARKER
    assert _truncate_text("abcdef" * 10, 0) == SHORT_TRUNCATION_MARKER
    # Text no longer than the short marker is never cut, so a cut cannot lengthen it.
    assert _truncate_text("abc", 0) == "abc"


@pytest.mark.parametrize("length", [1, 3, 4, 50, 120, 5_000])
def test_truncate_text_marks_every_cut_and_never_grows(length):
    """Every cut carries a marker, stays within ``keep`` (or the short marker's
    length when ``keep`` is smaller), and is never longer than the input."""
    text = "y" * length
    for keep in range(0, length):
        out = _truncate_text(text, keep)
        assert len(out) <= max(keep, len(SHORT_TRUNCATION_MARKER))
        assert len(out) <= len(text)
        if out != text:
            assert SHORT_TRUNCATION_MARKER in out or "characters truncated" in out


def test_compaction_without_tools_strips_images_then_truncates():
    """Images are stripped first when the history overflows; the remaining text is
    then cut to fit, and the no-tools request shape is used."""
    llm = CharCountingFakeLLM([_make_response(content="THE SUMMARY")], accept_up_to=8_000)
    history = _oversized_history()
    history[3]["content"] = [
        {"type": "text", "text": history[3]["content"]},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 400}},
    ]
    result = compact_conversation_history(original_conversation_history=history, llm=llm)  # type: ignore

    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"] is None
    sent = json.dumps(llm.calls[0]["messages"])
    assert "image(s) were present but stripped from compaction" in sent
    assert "data:image/png" not in sent
    assert result.input_truncated is True
    assert result.summary == "THE SUMMARY"


def _instruction_tokens(llm, truncated):
    """Tokens the fake counts for the summarization instructions."""
    text = load_and_render_prompt(prompt="builtin://conversation_history_compaction.jinja2", context={})
    if truncated:
        text = f"{text}\n\n{TRUNCATED_INPUT_INSTRUCTION}"
    return llm.count_tokens([{"role": "user", "content": text}]).total_tokens


@pytest.mark.parametrize("over_by", [1, 5, 20, 60])
def test_compaction_reserves_room_for_the_truncation_instruction(over_by):
    """A history barely over budget is cut just enough; the longer instructions
    sent with a cut history must still leave the output reserve free."""
    llm = CharCountingFakeLLM([_make_response(content="THE SUMMARY")])
    capacity = llm.context_window - llm.max_output
    llm.accept_up_to = capacity
    history = _oversized_history(tool_result_chars=(400,))
    room = capacity - _instruction_tokens(llm, truncated=False)
    history[3].pop("token_count")
    history[3]["content"] = ""
    history[3]["content"] = "x" * ((room - llm.count_tokens(history, _TOOLS).total_tokens + over_by) * 4)
    assert room < llm.count_tokens(history, _TOOLS).total_tokens <= room + over_by + 1

    result = compact_conversation_history(original_conversation_history=history, llm=llm, tools=_TOOLS)  # type: ignore

    assert result.input_truncated is True
    assert result.fallback_used is False
    assert _request_tokens(llm, llm.calls[0]) <= capacity


@pytest.mark.parametrize("spare", [0, 400, 5_000])
def test_compaction_budget_never_exceeds_remaining_capacity(monkeypatch, spare):
    """The history budget is what remains after the output reserve and the longer
    (truncated-input) instructions, with no floor that could overflow the window."""
    budgets = []
    real_fit = compaction._fit_history_to_token_budget

    def spy(messages, llm, tools, budget_tokens):
        budgets.append(budget_tokens)
        return real_fit(messages, llm, tools, budget_tokens)

    monkeypatch.setattr(compaction, "_fit_history_to_token_budget", spy)
    llm = CharCountingFakeLLM([_make_response(content="THE SUMMARY")], context_window=10_000)
    llm.max_output = 10_000 - _instruction_tokens(llm, truncated=True) - spare

    compact_conversation_history(original_conversation_history=_oversized_history(), llm=llm, tools=_TOOLS)  # type: ignore

    assert budgets[0] == spare


def test_fit_history_converges_with_many_short_messages():
    """Many messages shorter than the full marker can still be cut to the budget."""
    llm = CharCountingFakeLLM([])
    messages = [{"role": "user", "content": "z" * 60} for _ in range(400)]
    fitted, truncated, fits = _fit_history_to_token_budget(messages, llm, None, 1_000)

    assert truncated is True
    assert fits is True
    assert llm.count_tokens(fitted).total_tokens <= 1_000


def test_compaction_never_sends_a_history_over_budget():
    """An attempt whose history cannot be cut to its budget is skipped, not sent."""
    llm = CharCountingFakeLLM([_make_response(content="FALLBACK SUMMARY")], context_window=10_000)
    llm.max_output = 10_000 - _instruction_tokens(llm, truncated=True) - 2_000
    history = _oversized_history()
    # The system prompt alone exceeds the primary budget; the fallback drops it.
    history[0]["content"] = "s" * 12_000

    result = compact_conversation_history(original_conversation_history=history, llm=llm, tools=_TOOLS)  # type: ignore

    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"] is None
    assert _request_tokens(llm, llm.calls[0]) <= llm.context_window - llm.max_output
    assert result.summary == "FALLBACK SUMMARY"
    assert "could not be cut" in (result.fallback_reason or "")


def test_compaction_makes_no_call_when_nothing_can_fit():
    """With only uncuttable content over budget, no request is sent at all."""
    llm = CharCountingFakeLLM([], context_window=10_000)
    llm.max_output = 10_000 - _instruction_tokens(llm, truncated=True) - 200
    history = [{"role": "user", "content": "why?"}] + [{"role": "assistant", "content": None} for _ in range(1_000)]

    result = compact_conversation_history(original_conversation_history=history, llm=llm)  # type: ignore

    assert llm.calls == []
    assert result.messages_after_compaction == history
    assert result.summary is None


class _MarkerHeavyFakeLLM(CharCountingFakeLLM):
    """Counts each truncation marker as far more tokens than its characters, so
    the first truncation pass lands over budget and a second pass is needed."""

    def count_tokens(self, messages, tools=None):
        usage = super().count_tokens(messages, tools)
        markers = sum(json.dumps(m.get("content")).count("characters truncated") for m in messages)
        usage.total_tokens += 400 * markers
        return usage


def test_fit_history_marker_counts_everything_removed_across_passes():
    """A message cut over several passes carries one marker that counts every
    character removed from the original, not just the last pass's cut."""
    llm = _MarkerHeavyFakeLLM([])
    original = "Q" * 88_000
    messages = [
        {"role": "user", "content": "why?"},
        {"role": "tool", "tool_call_id": "c1", "content": original},
        {"role": "tool", "tool_call_id": "c2", "content": "W" * 60_000},
    ]
    passes = []
    real_count = llm.count_tokens

    def counting(messages, tools=None):
        passes.append(1)
        return real_count(messages, tools)

    llm.count_tokens = counting  # type: ignore[method-assign]
    fitted, truncated, fits = _fit_history_to_token_budget(messages, llm, None, 3_000)
    assert truncated and fits
    assert len(passes) >= 3  # initial count + at least two truncation passes

    cut = fitted[1]["content"]
    assert cut.count("characters truncated") == 1
    removed = int(cut.split("[... ")[1].split(" characters")[0])
    kept = len(cut) - len(TRUNCATION_MARKER.format(removed=removed))
    assert removed + kept == len(original)


def test_compaction_fallback_keeps_full_budget_after_non_size_failure():
    """A primary that failed for a reason other than size (ROB-424 gateways)
    falls back to the full, untruncated flattened history."""
    llm = CharCountingFakeLLM(
        [RuntimeError("400 toolConfig must be defined"), _make_response(content="FALLBACK SUMMARY")],
        context_window=16_000,
    )
    history = _oversized_history()
    # Fits the full budget but not half of it, so a halved fallback would cut it.
    budget = llm.context_window - llm.max_output - _instruction_tokens(llm, truncated=True)
    assert budget // 2 < llm.count_tokens(history, _TOOLS).total_tokens <= budget
    result = compact_conversation_history(original_conversation_history=history, llm=llm, tools=_TOOLS)  # type: ignore

    assert len(llm.calls) == 2
    assert "characters truncated" not in json.dumps(llm.calls[1]["messages"])
    assert TRUNCATED_INPUT_INSTRUCTION not in llm.calls[1]["messages"][-1]["content"]
    assert result.input_truncated is False
    assert COMPACTION_TRUNCATION_NOTE not in result.messages_after_compaction[1]["content"]


def test_compaction_fallback_stops_after_non_size_failure():
    """A fallback failing for a reason other than size is not retried smaller."""
    llm = CharCountingFakeLLM(
        [RuntimeError("400 The conversation is too long for the context window"), RuntimeError("502 bad gateway")],
        accept_up_to=None,
    )
    result = compact_conversation_history(original_conversation_history=_oversized_history(), llm=llm, tools=_TOOLS)  # type: ignore

    assert len(llm.calls) == 2
    assert "budget 50%" in (result.fallback_reason or "")
    assert "budget 25%" not in (result.fallback_reason or "")


def test_compaction_input_truncated_reflects_only_the_sent_request():
    """A primary that was never sent (it could not fit) does not mark a summary
    produced from an untruncated fallback as truncated."""
    llm = CharCountingFakeLLM([_make_response(content="FALLBACK SUMMARY")], context_window=20_000)
    history = _oversized_history(tool_result_chars=(400,))
    history[0]["content"] = "s" * 80_000  # only the system prompt is too big; the fallback drops it

    result = compact_conversation_history(original_conversation_history=history, llm=llm, tools=_TOOLS)  # type: ignore

    assert len(llm.calls) == 1
    assert "characters truncated" not in json.dumps(llm.calls[0]["messages"])
    assert result.fallback_used is True
    assert result.input_truncated is False
    assert COMPACTION_TRUNCATION_NOTE not in result.messages_after_compaction[1]["content"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (RuntimeError("Error code: 400 - The conversation is too long for the AI model's context window."), True),
        (RuntimeError("prompt is too long: 213000 tokens > 200000 maximum"), True),
        (RuntimeError("This model's maximum context length is 128000 tokens"), True),
        (RuntimeError("input length and `max_tokens` exceed context limit"), True),
        (RuntimeError("400 toolConfig must be defined"), False),
        (RuntimeError("502 bad gateway"), False),
    ],
)
def test_is_context_length_error(error, expected):
    """Context-length rejections are recognised whatever the provider's wording."""
    assert _is_context_length_error(error) is expected


def test_fit_history_keeps_most_of_the_budget_when_cutting_deep():
    """Cutting a history far over budget keeps close to the budget's worth of
    text instead of overshooting to nothing."""
    llm = CharCountingFakeLLM([])
    messages = [{"role": "tool", "tool_call_id": "c1", "content": "Q" * 148_000}]
    fitted, truncated, fits = _fit_history_to_token_budget(messages, llm, None, 3_000)

    assert truncated and fits
    assert llm.count_tokens(fitted).total_tokens >= 2_500
