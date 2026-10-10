"""Fake LLM that counts tokens by text size and enforces a context window, as a provider does."""

import json

from litellm.types.utils import Choices, Message, ModelResponse

from holmes.core.llm import ContextWindowUsage


def make_response(content=None, tool_calls=None):
    """Build a minimal litellm ModelResponse wrapping one assistant message."""
    return ModelResponse(choices=[Choices(message=Message(content=content, role="assistant", tool_calls=tool_calls))])


class CharCountingFakeLLM:
    """Fake LLM whose token count is proportional to message text, and which
    rejects requests larger than ``accept_up_to`` tokens like a real provider.
    Without canned responses left it answers "SUMMARY <n>"."""

    def __init__(self, responses, context_window=10_000, max_output=2_000, accept_up_to=None):
        """Configure the window, output reserve and the provider-side size limit."""
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.context_window = context_window
        self.max_output = max_output
        self.accept_up_to = accept_up_to
        self.accepted: list[dict] = []

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
        """Reject requests over ``accept_up_to`` tokens, else replay or make up a summary."""
        call = {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        self.calls.append(call)
        if self.accept_up_to is not None and self.count_tokens(messages, tools).total_tokens > self.accept_up_to:
            raise RuntimeError("400 The conversation is too long for the AI model's context window.")
        result = self.responses.pop(0) if self.responses else make_response(content=f"SUMMARY {len(self.calls)}")
        if isinstance(result, Exception):
            raise result
        self.accepted.append(call)
        return result

    def get_context_window_size(self):
        """Return the configured context window."""
        return self.context_window

    def get_maximum_output_token(self):
        """Return the configured output reserve."""
        return self.max_output

    def get_max_token_count_for_single_tool(self):
        """Per-tool output cap; outputs of half of it or more count as large."""
        return 2_000


FILLER = "~"


def oversized_history(tool_result_chars=(30_000, 12_000, 400)):
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
                "content": f"HEAD-{i} " + FILLER * size + f" TAIL-{i}",
            }
        )
    history.append({"role": "user", "content": "what did you find?"})
    return history
