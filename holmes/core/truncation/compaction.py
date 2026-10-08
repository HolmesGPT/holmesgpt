"""
LLM-based conversation history compaction — summarizes old messages to free context space.

For an overview of all context management mechanisms, see:
docs/reference/context-management.md
"""

import logging
import re
from typing import Any, Callable, Optional

from litellm.exceptions import ContextWindowExceededError
from litellm.types.utils import ModelResponse
from pydantic import BaseModel
from tenacity import Retrying, retry_if_exception, stop_after_attempt

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
    # Requests the summary took; more than one means the history was summarized in chunks.
    summarization_requests: int = 0
    # Tool calls with outputs near the per-tool cap, set only when the history
    # did not fit one summarization request: fetching them again would overflow it again.
    overflowing_tool_call_ids: list[str] = []


COMPACTION_SUMMARY_PREAMBLE = (
    "The conversation history was compacted to preserve available space in the "
    "context window. The summary below replaces the earlier portion of the conversation:"
)
COMPACTION_SUMMARY_SUFFIX = (
    "Continue the conversation from where it left off, using the summary above as "
    "established context."
)
MESSAGE_PART_LABEL = "[part {index} of {count} of a message split to fit the summarization request]"


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


# A request the provider rejects as too long is resent with half the budget:
# the local token count can undercount the provider's tokenizer
# by a third or more, so a request that fits locally can still be rejected.
_MAX_SUMMARIZATION_ATTEMPTS = 3
# Gateways (e.g. the Robusta AI gateway) return context-length rejections as a
# plain 400 that litellm does not map to ContextWindowExceededError.
_CONTEXT_LENGTH_ERROR = re.compile(
    r"context.?(window|length|limit)|(prompt|input|request) (is )?too long|too many (input )?tokens"
    r"|maximum context|exceeds? the (model|context|maximum)",
    re.IGNORECASE,
)


def _is_context_length_error(error: BaseException) -> bool:
    """Whether a provider rejected the request for exceeding its context window."""
    return isinstance(error, ContextWindowExceededError) or bool(
        _CONTEXT_LENGTH_ERROR.search(str(error))
    )


def _message_tokens(message: dict, llm: LLM) -> int:
    return llm.count_tokens(messages=[message]).total_tokens


def _split_text(text: str, fits: Callable[[str], bool]) -> list[str]:
    """Halve ``text`` until every part fits; at least one character per part."""
    if len(text) <= 1 or fits(text):
        return [text]
    middle = len(text) // 2
    return _split_text(text[:middle], fits) + _split_text(text[middle:], fits)


def _split_message(message: dict, llm: LLM, max_tokens: int) -> list[dict]:
    """Split a message larger than ``max_tokens`` into consecutive labelled parts.

    Non-text blocks (images) stay on the first part.
    """
    if _message_tokens(message, llm) <= max_tokens:
        return [message]
    content = message.get("content")
    if isinstance(content, list):
        text = "\n".join(
            block.get("text") or ""
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        other_blocks = [b for b in content if not (isinstance(b, dict) and b.get("type") == "text")]
    else:
        text, other_blocks = content or "", []
    if not text:
        return [message]

    base = {k: v for k, v in message.items() if k not in ("content", "token_count")}
    widest_label = MESSAGE_PART_LABEL.format(index=999, count=999)

    def fits(part_text: str) -> bool:
        return _message_tokens({**base, "content": f"{widest_label}\n{part_text}"}, llm) <= max_tokens

    if not fits(""):
        return [message]  # the overhead alone is too big; splitting cannot help
    # Cut into roughly fitting slices first so only the few that still overflow are halved.
    slices = -(-_message_tokens(message, llm) // max_tokens)
    size = -(-len(text) // slices)
    texts = [part for start in range(0, len(text), size) for part in _split_text(text[start : start + size], fits)]
    parts = []
    for index, part_text in enumerate(texts, start=1):
        labelled = f"{MESSAGE_PART_LABEL.format(index=index, count=len(texts))}\n{part_text}"
        part_content: Any = labelled
        if index == 1 and other_blocks:
            part_content = [{"type": "text", "text": labelled}, *other_blocks]
        parts.append({**base, "content": part_content})
    return parts


def _large_tool_call_ids(messages: list[dict], llm: LLM) -> list[str]:
    """Ids of tool messages holding at least half of the per-tool output cap."""
    threshold = llm.get_max_token_count_for_single_tool() // 2
    return [
        message["tool_call_id"]
        for message in messages
        if message.get("role") == "tool"
        and message.get("tool_call_id")
        and _message_tokens(message, llm) >= threshold
    ]


def _summary_message(summary_text: str, continue_directive: bool) -> dict:
    """The user message that carries a summary in place of the history it covers."""
    content = f"{COMPACTION_SUMMARY_PREAMBLE}\n\n{summary_text}"
    if continue_directive:
        content = f"{content}\n\n{COMPACTION_SUMMARY_SUFFIX}"
    return {"role": "user", "content": content}


def _summarize_in_chunks(
    messages: list[dict],
    llm: LLM,
    budget_tokens: int,
    instructions_message: dict,
    usage: RequestStats,
) -> tuple[str, int]:
    """Summarize ``messages`` in as many requests as it takes to stay within ``budget_tokens``.

    Each request carries the summary so far followed by as many of the next
    messages as fit, so no message is dropped. Messages are split into parts of
    at most a quarter of the budget, leaving room for the summary they follow. A
    request rejected as too long is resent with half the budget, which then
    applies to the remaining requests too; the summary already made is kept.
    Returns (summary, number of requests); usage is added to ``usage``.
    """
    parts = [part for message in messages for part in _split_message(message, llm, budget_tokens // 4)]
    part_tokens = [_message_tokens(part, llm) for part in parts]
    summary = ""
    requests = 0
    next_part = 0
    sent = 0
    # A rejected request that held a single part cannot be made smaller.
    retrying = Retrying(
        retry=retry_if_exception(_is_context_length_error),
        stop=stop_after_attempt(_MAX_SUMMARIZATION_ATTEMPTS) | (lambda _: sent == 1),
        reraise=True,
    )
    while next_part < len(parts):
        prefix = [_summary_message(summary, continue_directive=False)] if summary else []
        prefix_tokens = sum(_message_tokens(m, llm) for m in prefix)
        for attempt in retrying:
            with attempt:
                if attempt.retry_state.attempt_number > 1:
                    budget_tokens //= 2
                sent = 1
                used = prefix_tokens + part_tokens[next_part]
                while next_part + sent < len(parts) and used + part_tokens[next_part + sent] <= budget_tokens:
                    used += part_tokens[next_part + sent]
                    sent += 1
                response = llm.completion(
                    messages=[*prefix, *parts[next_part : next_part + sent], instructions_message],
                    drop_params=True,
                )  # type: ignore
        usage += RequestStats.from_response(response)
        requests += 1
        next_part += sent
        summary = _extract_text_content(_get_response_message(response)).strip()  # type: ignore[arg-type]
        if not summary:
            raise ValueError(f"summarization request {requests} returned no text")
    return summary, requests


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
    we fall back to tool messages flattened to text and no tools attached, which
    every OpenAI-compatible gateway accepts.

    A history too big for one request skips the primary and goes straight to the
    flattened form, summarized in chunks: each request carries the summary so far
    and the next messages that fit, so nothing is dropped (ROB-1519). A chunk the
    provider rejects as too long is resent, and the rest are sent, at half the
    budget; any other failure ends compaction.

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
    images_stripped = False

    if image_tokens > 0 and (total_tokens + instruction_tokens + maximum_output_token) <= context_window:
        logging.info(
            f"Compaction: keeping {image_tokens} image tokens "
            f"(conversation fits in context window: {total_tokens} + {instruction_tokens} + {maximum_output_token} <= {context_window})"
        )
    elif image_tokens > 0:
        images_stripped = True
        logging.info(
            f"Compaction: stripping {image_tokens} image tokens "
            f"(conversation would overflow: {total_tokens} + {instruction_tokens} + {maximum_output_token} > {context_window})"
        )
        conversation_history = _strip_images_for_compaction(conversation_history)

    instructions_message = {"role": "user", "content": compaction_instructions}
    compaction_usage = RequestStats()
    # The summarization request must itself fit the window, or the provider
    # rejects it and compaction can never recover (ROB-1519).
    input_budget = max(0, context_window - maximum_output_token - instruction_tokens)
    if images_stripped:
        total_tokens = llm.count_tokens(messages=conversation_history, tools=tools).total_tokens  # type: ignore

    summary_text = ""
    summarization_requests = 0
    fallback_reason: Optional[str] = None
    history_overflowed = total_tokens > input_budget
    if history_overflowed:
        fallback_reason = f"history ({total_tokens} tokens) exceeds the {input_budget}-token budget of one request"
    else:
        try:
            if tools:
                response: Optional[ModelResponse] = llm.completion(
                    messages=[*conversation_history, instructions_message],
                    tools=tools,
                    tool_choice="auto",
                    drop_params=True,
                )  # type: ignore
            else:
                response = llm.completion(
                    messages=[*conversation_history, instructions_message], drop_params=True
                )  # type: ignore
            compaction_usage += RequestStats.from_response(response)
            response_message = _get_response_message(response)
            if response_message is None:
                fallback_reason = "no message in summarization response"
            elif getattr(response_message, "tool_calls", None):
                fallback_reason = "model responded with a tool call instead of a summary"
            elif not (summary_text := _extract_text_content(response_message).strip()):
                fallback_reason = "summarization response contains no text"
            else:
                summarization_requests = 1
        except Exception as e:
            fallback_reason = f"summarization request failed: {e}"
            history_overflowed = _is_context_length_error(e)

    if fallback_reason:
        # Some gateways mis-translate tool blocks / tools (ROB-424, reported on
        # Kong AI Gateway and vanilla LiteLLM proxies); flattened text with no
        # tools is accepted by every OpenAI-compatible endpoint. A history too big
        # for one request is summarized in chunks so nothing is dropped.
        logging.log(
            logging.INFO if history_overflowed else logging.WARNING,
            f"Compaction: {fallback_reason}; summarizing tool messages flattened to text, "
            "without tools, in as many requests as needed",
        )
        summary_text = ""
        if history_overflowed and not images_stripped:
            conversation_history = _strip_images_for_compaction(conversation_history)
        flattened_history, _ = strip_system_prompt(conversation_history)
        flattened_history = _flatten_tool_messages_for_compaction(flattened_history)
        try:
            summary_text, summarization_requests = _summarize_in_chunks(
                flattened_history, llm, input_budget, instructions_message, compaction_usage
            )
        except Exception as e:
            # Degrade gracefully via the empty-summary path below (original
            # history returned unchanged) instead of aborting the whole turn.
            fallback_reason = f"{fallback_reason}; fallback summarization failed: {e}"
            summary_text = ""

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
    compacted_conversation_history.append(_summary_message(summary_text, continue_directive=True))

    last_user_prompt = find_last_user_prompt(original_conversation_history)
    if last_user_prompt:
        compacted_conversation_history.append(last_user_prompt)

    return CompactionResult(
        messages_after_compaction=compacted_conversation_history,
        usage=compaction_usage,
        summary=summary_text,
        fallback_used=bool(fallback_reason),
        fallback_reason=fallback_reason,
        summarization_requests=summarization_requests,
        overflowing_tool_call_ids=(
            _large_tool_call_ids(original_conversation_history, llm) if history_overflowed else []
        ),
    )
