"""
LLM-based conversation history compaction — summarizes old messages to free context space.

For an overview of all context management mechanisms, see:
docs/reference/context-management.md
"""

import logging
from typing import Any, Optional

from litellm.types.utils import ModelResponse
from pydantic import BaseModel

from holmes.core.llm import LLM
from holmes.core.llm_usage import RequestStats
from holmes.plugins.prompts import load_and_render_prompt


class CompactionResult(BaseModel):
    """Result of conversation history compaction."""

    messages_after_compaction: list[dict]
    usage: Optional[RequestStats] = None
    summary: Optional[str] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None


COMPACTION_SUMMARY_PREAMBLE = (
    "The conversation history was compacted to preserve available space in the "
    "context window. The summary below replaces the earlier portion of the conversation:"
)
COMPACTION_SUMMARY_SUFFIX = (
    "Continue the conversation from where it left off, using the summary above as "
    "established context."
)


def strip_system_prompt(
    conversation_history: list[dict],
) -> tuple[list[dict], Optional[dict]]:
    """Split off the leading system message, returning (rest, system_message)."""
    if not conversation_history:
        return conversation_history, None
    first_message = conversation_history[0]
    if first_message and first_message.get("role") == "system":
        return conversation_history[1:], first_message
    return conversation_history[:], None


def find_last_user_prompt(conversation_history: list[dict]) -> Optional[dict]:
    """Return the last user message in the conversation, if any."""
    if not conversation_history:
        return None
    last_user_prompt: Optional[dict] = None
    for message in conversation_history:
        if message.get("role") == "user":
            last_user_prompt = message
    return last_user_prompt


def _count_image_tokens_in_messages(messages: list[dict], llm: LLM) -> int:
    """Count total tokens used by image blocks across all messages."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        # Count tokens for a synthetic message containing only image blocks
        image_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "image_url"]
        if image_blocks:
            synthetic = {"role": "user", "content": image_blocks}
            total += llm.count_tokens(messages=[synthetic]).total_tokens
    return total


def _strip_images_for_compaction(messages: list[dict]) -> list[dict]:
    """Strip image_url blocks from messages, replacing with a count placeholder."""
    stripped: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            stripped.append(msg)
            continue
        new_content: list[dict[str, Any]] = []
        image_count = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                image_count += 1
            else:
                new_content.append(block)
        if image_count > 0:
            new_content.append({
                "type": "text",
                "text": f"[{image_count} image(s) were present but stripped from compaction]",
            })
        new_msg = dict(msg)
        new_msg["content"] = new_content
        new_msg.pop("token_count", None)
        stripped.append(new_msg)
    return stripped


def _append_text_to_content(content: Any, text: str) -> Any:
    """Append a text snippet to a message content (str, block list, or None)."""
    if isinstance(content, list):
        return [*content, {"type": "text", "text": text}]
    if not content:
        return text
    return f"{content}\n{text}"


def _prepend_text_to_content(content: Any, text: str) -> Any:
    """Prepend a text snippet to a message content (str, block list, or None)."""
    if isinstance(content, list):
        return [{"type": "text", "text": text}, *content]
    if not content:
        return text
    return f"{text}\n{content}"


def _flatten_tool_messages_for_compaction(messages: list[dict]) -> list[dict]:
    """Rewrite tool_call / tool-result *blocks* as plain text for the compaction call.

    Used by the fallback summarization attempt, which sends no ``tools`` param.
    Bedrock Converse — and gateways that translate to it (Kong AI Gateway, a
    LiteLLM proxy, etc.) — reject any request whose messages contain
    tool-use/tool-result blocks without a ``toolConfig``:
    ``"The toolConfig field must be defined when using toolUse and toolResult
    content blocks."`` (see ROB-424).

    Flattening the blocks to text removes that requirement for every downstream
    gateway, and loses nothing the summarizer uses: the compaction prompt's
    "Tool Calls" section already asks the model to enumerate tool calls (with full
    arguments) and their outcomes as text. Image blocks are preserved so the
    image-handling logic above still applies.
    """
    flattened: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        tool_calls = msg.get("tool_calls")
        if role == "tool":
            # Orphaned tool result -> plain user text (keeps the result content,
            # including any image blocks, for the summarizer).
            new_msg = dict(msg)
            new_msg.pop("token_count", None)
            tool_call_id = new_msg.pop("tool_call_id", None)
            new_msg.pop("name", None)
            new_msg["role"] = "user"
            label = f"[tool result{f' for {tool_call_id}' if tool_call_id else ''}]"
            new_msg["content"] = _prepend_text_to_content(msg.get("content"), label)
            flattened.append(new_msg)
        elif role == "assistant" and isinstance(tool_calls, list) and tool_calls:
            new_msg = dict(msg)
            new_msg.pop("token_count", None)
            new_msg.pop("tool_calls", None)
            calls_text = "\n".join(
                f"[tool call] {tc.get('function', {}).get('name', '')} "
                f"{tc.get('function', {}).get('arguments', '')}".rstrip()
                for tc in tool_calls
                if isinstance(tc, dict)
            )
            new_msg["content"] = _append_text_to_content(msg.get("content"), calls_text)
            flattened.append(new_msg)
        else:
            flattened.append(msg)
    return flattened


def _get_response_message(response: Optional[ModelResponse]) -> Optional[Any]:
    """Return the first choice's message from a completion response, if any."""
    if (
        response
        and response.choices
        and response.choices[0]
        and response.choices[0].message  # type:ignore
    ):
        return response.choices[0].message  # type:ignore
    return None


def _extract_text_content(message: Any) -> str:
    """Extract plain text from a response message (content may be a str or a block list)."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _partially_compact(
    messages: list[dict], llm: LLM, tools: Optional[list[dict[str, Any]]]
) -> CompactionResult:
    """Compact the first half, then compact the remainder together with its summary."""
    half = max(len(messages) // 2, 4)  # >=4 so the remainder always shrinks
    while half < len(messages) and messages[half].get("role") == "tool":
        half += 1  # never split a tool call from its results
    if half >= len(messages):
        return CompactionResult(
            messages_after_compaction=messages,
            fallback_used=True,
            fallback_reason="conversation history cannot be split for partial compaction",
        )
    first = compact_conversation_history(messages[:half], llm, tools)
    if not first.summary:
        return CompactionResult(
            messages_after_compaction=messages,
            usage=first.usage,
            fallback_used=True,
            fallback_reason=first.fallback_reason,
        )
    return compact_conversation_history(
        first.messages_after_compaction + messages[half:], llm, tools
    )


def compact_conversation_history(
    original_conversation_history: list[dict],
    llm: LLM,
    tools: Optional[list[dict[str, Any]]] = None,
) -> CompactionResult:
    """
    Summarize the conversation and replace it with:
      1. Original system prompt, uncompacted (if present)
      2. Summary of the conversation so far (role=user, ending with a continue directive)
      3. Last user prompt, uncompacted (if present)

    The summarization request keeps the conversation in its native shape (tool
    blocks included) and attaches the agentic loop's ``tools``: gateways that
    translate to Bedrock Converse require ``toolConfig`` whenever messages contain
    tool-use/tool-result blocks (ROB-424), and reusing the exact shape of the
    previous agentic call also lets this request — the largest one Holmes makes —
    reuse that call's prompt-cache prefix. The compaction prompt instructs the
    model not to call tools; if it calls one anyway, or the request is rejected,
    we retry once with tool messages flattened to text and no tools attached,
    which every OpenAI-compatible gateway accepts.

    The summary is stored as a *user* message (as Claude Code does) rather than an
    assistant message, with no trailing system sentinel: an assistant summary
    produced under extended thinking carries signed thinking blocks, and a trailing
    system message gets hoisted by litellm — fusing the summary into the next
    assistant turn, which Bedrock rejects with "`thinking` or `redacted_thinking`
    blocks in the latest assistant message cannot be modified" (ROB-665, ROB-425).
    """
    _, system_prompt_message = strip_system_prompt(original_conversation_history)
    compaction_instructions = load_and_render_prompt(
        prompt="builtin://conversation_history_compaction.jinja2", context={}
    )
    conversation_history = original_conversation_history[:]

    # Decide whether to keep images in the compaction input.
    # Keep them if the conversation (with images) fits in the compaction LLM's
    # context window, so it can describe what was in them. Otherwise strip them.
    # Include instruction tokens in the budget since they are appended before the LLM call.
    context_window = llm.get_context_window_size()
    maximum_output_token = llm.get_maximum_output_token()
    instruction_tokens = llm.count_tokens(
        messages=[{"role": "user", "content": compaction_instructions}]
    ).total_tokens
    total_tokens = llm.count_tokens(messages=conversation_history, tools=tools).total_tokens  # type: ignore
    image_tokens = _count_image_tokens_in_messages(conversation_history, llm)

    if image_tokens > 0 and (total_tokens + instruction_tokens + maximum_output_token) <= context_window:
        logging.info(
            f"Compaction: keeping {image_tokens} image tokens "
            f"(conversation fits in context window: {total_tokens} + {instruction_tokens} + {maximum_output_token} <= {context_window})"
        )
    elif image_tokens > 0:
        logging.info(
            f"Compaction: stripping {image_tokens} image tokens "
            f"(conversation would overflow: {total_tokens} + {instruction_tokens} + {maximum_output_token} > {context_window})"
        )
        conversation_history = _strip_images_for_compaction(conversation_history)

    total_tokens = llm.count_tokens(messages=conversation_history, tools=tools).total_tokens  # type: ignore
    if total_tokens + instruction_tokens + maximum_output_token > context_window:
        return _partially_compact(conversation_history, llm, tools)

    instructions_message = {"role": "user", "content": compaction_instructions}
    compaction_usage = RequestStats()

    response_message = None
    fallback_reason: Optional[str] = None
    try:
        if tools:
            response: Optional[ModelResponse] = llm.completion(
                messages=conversation_history + [instructions_message],
                tools=tools,
                tool_choice="auto",
                drop_params=True,
            )  # type: ignore
        else:
            response = llm.completion(
                messages=conversation_history + [instructions_message], drop_params=True
            )  # type: ignore
        compaction_usage += RequestStats.from_response(response)
        response_message = _get_response_message(response)
        if response_message is None:
            fallback_reason = "no message in summarization response"
        elif getattr(response_message, "tool_calls", None):
            fallback_reason = "model responded with a tool call instead of a summary"
        elif not _extract_text_content(response_message).strip():
            fallback_reason = "summarization response contains no text"
    except Exception as e:
        fallback_reason = f"summarization request failed: {e}"

    if fallback_reason:
        # Compatibility fallback: some gateways mis-translate tool blocks / tools
        # (ROB-424 was reported on Kong AI Gateway and vanilla LiteLLM proxies).
        # Flattening tool messages to text and sending no tools is accepted by
        # every OpenAI-compatible endpoint.
        logging.warning(
            f"Compaction: primary summarization attempt unusable ({fallback_reason}); "
            "retrying with tool messages flattened to text and no tools attached"
        )
        flattened_history, _ = strip_system_prompt(conversation_history)
        flattened_history = _flatten_tool_messages_for_compaction(flattened_history)
        try:
            response = llm.completion(
                messages=flattened_history + [instructions_message], drop_params=True
            )  # type: ignore
            compaction_usage += RequestStats.from_response(response)
            response_message = _get_response_message(response)
        except Exception as e:
            # Both attempts failed — degrade gracefully via the empty-summary
            # path below (original history returned unchanged) instead of
            # aborting the whole turn.
            fallback_reason = f"{fallback_reason}; fallback request also failed: {e}"
            response_message = None

    summary_text = (
        _extract_text_content(response_message).strip() if response_message else ""
    )
    if not summary_text:
        logging.error(
            "Failed to compact conversation history. Unexpected LLM's response for compaction"
        )
        return CompactionResult(
            messages_after_compaction=original_conversation_history,
            usage=compaction_usage,
            fallback_used=bool(fallback_reason),
            fallback_reason=fallback_reason,
        )

    compacted_conversation_history: list[dict] = []
    if system_prompt_message:
        compacted_conversation_history.append(system_prompt_message)

    # The summary goes into history as a *user* message built from the response's
    # text only — thinking blocks / provider-specific fields must never be replayed.
    compacted_conversation_history.append(
        {
            "role": "user",
            "content": f"{COMPACTION_SUMMARY_PREAMBLE}\n\n{summary_text}\n\n{COMPACTION_SUMMARY_SUFFIX}",
        }
    )

    last_user_prompt = find_last_user_prompt(original_conversation_history)
    if last_user_prompt:
        compacted_conversation_history.append(last_user_prompt)

    return CompactionResult(
        messages_after_compaction=compacted_conversation_history,
        usage=compaction_usage,
        summary=summary_text,
        fallback_used=bool(fallback_reason),
        fallback_reason=fallback_reason,
    )
