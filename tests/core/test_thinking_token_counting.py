"""Reasoning tokens must be counted as context.

An extended-thinking response carries the model's reasoning as `thinking_blocks`,
and some providers also set a `reasoning_content` string. litellm's token counter
reads `reasoning_content` but ignores `thinking_blocks`, so a message that only
has the blocks used to be counted as if the reasoning weren't there - while it
stays in the conversation and is sent back to the provider (and billed as input)
on every later turn. That undercount made the reported context usage smaller than
what the model was really carrying.
"""

from unittest.mock import patch

import litellm
import pytest

from holmes.core.llm import DefaultLLM, _thinking_blocks_text


MODEL = "claude-sonnet-4-20250514"
# Long enough that a miscount is unmistakable, short enough to stay fast.
THINKING_TEXT = "deliberating carefully about the failing pod " * 200


def _make_llm(model: str = MODEL) -> DefaultLLM:
    with patch.object(DefaultLLM, "check_llm"):
        return DefaultLLM(model=model, api_key="fake-key")


def _tokens_of(text: str, model: str = MODEL) -> int:
    return litellm.token_counter(model=model, messages=[{"role": "assistant", "content": text}])


THINKING_TOKENS = _tokens_of(THINKING_TEXT)

# Sanity: the fixture is big enough for the assertions below to mean something.
assert THINKING_TOKENS > 500


def _answer(**extra) -> dict:
    return {"role": "assistant", "content": "The pod is OOMKilled.", **extra}


def _blocks(text: str = THINKING_TEXT) -> list[dict]:
    return [{"type": "thinking", "thinking": text, "signature": "sig"}]


# ---------- _thinking_blocks_text ----------


@pytest.mark.parametrize(
    "message, expected",
    [
        (_answer(), ""),
        (_answer(thinking_blocks=[]), ""),
        (_answer(thinking_blocks=_blocks("abc")), "abc"),
        (
            _answer(
                thinking_blocks=[
                    {"type": "thinking", "thinking": "abc"},
                    {"type": "redacted_thinking", "data": "def"},
                ]
            ),
            "abcdef",
        ),
        (_answer(thinking_blocks="not-a-list"), ""),
        (_answer(thinking_blocks=[None, {"type": "thinking"}]), ""),
    ],
    ids=["absent", "empty", "single", "redacted", "wrong_type", "malformed_blocks"],
)
def test_thinking_blocks_text(message: dict, expected: str):
    assert _thinking_blocks_text(message) == expected


# ---------- count_tokens ----------


def test_thinking_blocks_are_counted():
    llm = _make_llm()
    plain = llm.count_tokens(messages=[_answer()])
    with_thinking = llm.count_tokens(messages=[_answer(thinking_blocks=_blocks())])

    added = with_thinking.total_tokens - plain.total_tokens
    assert added == pytest.approx(THINKING_TOKENS, rel=0.05)
    # Reasoning belongs to the assistant turn that produced it.
    assert with_thinking.assistant_tokens - plain.assistant_tokens == added


def test_reasoning_content_is_not_double_counted():
    """litellm already counts reasoning_content; providers that send both
    representations must not be charged twice."""
    llm = _make_llm()
    both = llm.count_tokens(
        messages=[_answer(reasoning_content=THINKING_TEXT, thinking_blocks=_blocks())]
    )
    only_reasoning = llm.count_tokens(messages=[_answer(reasoning_content=THINKING_TEXT)])

    assert both.total_tokens == only_reasoning.total_tokens


def test_thinking_tokens_included_in_cached_recount():
    """The per-message cache holds the corrected count, and a recount over the
    same messages stays stable instead of dropping the reasoning."""
    llm = _make_llm()
    messages = [_answer(thinking_blocks=_blocks())]

    first = llm.count_tokens(messages=messages)
    assert messages[0]["token_count"] >= THINKING_TOKENS
    second = llm.count_tokens(messages=messages)

    assert second.total_tokens == first.total_tokens
    assert second.assistant_tokens == first.assistant_tokens


def test_thinking_tokens_do_not_leak_into_tool_definitions():
    """tools_to_call_tokens is derived by subtraction, so reasoning tokens must be
    added to the total only after it is computed."""
    llm = _make_llm()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "kubectl_get",
                "description": "get resources",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            },
        }
    ]
    without = llm.count_tokens(messages=[_answer()], tools=tools)
    with_thinking = llm.count_tokens(
        messages=[_answer(thinking_blocks=_blocks())], tools=tools
    )

    assert with_thinking.tools_to_call_tokens == without.tools_to_call_tokens
    assert with_thinking.total_tokens > without.total_tokens


def test_non_anthropic_model_also_counts_thinking():
    """Reasoning blocks are not Anthropic-specific in the message schema."""
    llm = _make_llm(model="gpt-4o")
    plain = llm.count_tokens(messages=[_answer()])
    with_thinking = llm.count_tokens(messages=[_answer(thinking_blocks=_blocks())])

    assert with_thinking.total_tokens > plain.total_tokens
