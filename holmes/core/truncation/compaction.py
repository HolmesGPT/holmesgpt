"""
LLM-based conversation history compaction — summarizes old messages to free context space.

For an overview of all context management mechanisms, see:
docs/reference/context-management.md
"""

import logging
import math
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
    input_truncated: bool = False


COMPACTION_SUMMARY_PREAMBLE = (
    "The conversation history was compacted to preserve available space in the "
    "context window. The summary below replaces the earlier portion of the conversation:"
)
COMPACTION_SUMMARY_SUFFIX = (
    "Continue the conversation from where it left off, using the summary above as "
    "established context."
)
TRUNCATED_INPUT_INSTRUCTION = (
    "Some conversation text above was cut to fit this request; each cut is marked "
    "'characters truncated to fit the compaction request' (or '[…]' when short). For "
    "each such message, say in the summary that it was only partially seen, and never "
    "state that something is absent from its original text."
)
COMPACTION_TRUNCATION_NOTE = (
    "Some conversation text was cut before this summary was written, so the summary "
    "may omit parts of the affected messages. Do not conclude that information is "
    "absent from those messages. If omitted tool output matters, re-run the tool with "
    "a narrower query (for example, filtering with grep) instead of fetching the full "
    "output again."
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


# Each fallback attempt halves the input budget: the local token count can
# undercount the provider's tokenizer by a third or more (seen on Claude), so a
# history that "fits" locally can still be rejected as too long.
FALLBACK_BUDGET_FRACTIONS = (0.5, 0.25)
TRUNCATION_MARKER = "\n[... {removed} characters truncated to fit the compaction request; not shown to the summarizer ...]\n"
SHORT_TRUNCATION_MARKER = "[…]"
_MAX_TRUNCATION_PASSES = 5


def _text_length(message: dict) -> int:
    """Number of characters of text in a message's content."""
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return 0


def _truncate_text(text: str, keep: int) -> str:
    """Keep the head and tail of ``text`` in at most ``keep`` characters, marker included.
    Below the full marker's length a short one is used, and none below that."""
    if len(text) <= keep:
        return text
    if len(TRUNCATION_MARKER.format(removed=len(text))) <= keep:
        body = keep - len(TRUNCATION_MARKER.format(removed=len(text)))
        marker = TRUNCATION_MARKER.format(removed=len(text) - body)
    else:
        marker = SHORT_TRUNCATION_MARKER if len(SHORT_TRUNCATION_MARKER) <= keep else ""
        body = keep - len(marker)
    head = body - body // 2
    tail = body // 2
    return text[:head] + marker + (text[-tail:] if tail else "")


def _truncate_message_text(message: dict, keep: int) -> dict:
    """Return a copy of ``message`` with at most ``keep`` characters of text."""
    length = _text_length(message)
    if length <= keep:
        return message
    new_msg = dict(message)
    new_msg.pop("token_count", None)
    content = message["content"]
    if isinstance(content, str):
        new_msg["content"] = _truncate_text(content, keep)
    else:
        is_text = [isinstance(block, dict) and block.get("type") == "text" for block in content]
        block_cap = _water_fill_cap(
            [len(block.get("text") or "") for block, text in zip(content, is_text) if text], length - keep
        )
        new_msg["content"] = [
            {**block, "text": _truncate_text(block.get("text") or "", block_cap)} if text else block
            for block, text in zip(content, is_text)
        ]
    return new_msg


def _water_fill_cap(lengths: list[int], chars_to_remove: int) -> int:
    """Largest per-message cap that removes at least ``chars_to_remove`` characters,
    cutting only the longest messages."""
    remaining = sorted(lengths, reverse=True)
    removed_above_cap = 0
    for i, length in enumerate(remaining):
        next_length = remaining[i + 1] if i + 1 < len(remaining) else 0
        # Lowering the cap from `length` to `next_length` trims the i+1 longest messages.
        step = (length - next_length) * (i + 1)
        if removed_above_cap + step >= chars_to_remove:
            return length - math.ceil((chars_to_remove - removed_above_cap) / (i + 1))
        removed_above_cap += step
    return 0


def _fit_history_to_token_budget(
    messages: list[dict],
    llm: LLM,
    tools: Optional[list[dict[str, Any]]],
    budget_tokens: int,
) -> tuple[list[dict], bool, bool]:
    """Truncate the longest message texts until the history fits ``budget_tokens``.

    A leading system message is never truncated. Returns (messages, truncated,
    fits); ``fits`` is False when non-text content alone exceeds the budget.
    """
    total = llm.count_tokens(messages=messages, tools=tools).total_tokens  # type: ignore
    if total <= budget_tokens:
        return messages, False, True

    has_system = bool(messages) and messages[0].get("role") == "system"
    truncatable_from = 1 if has_system else 0
    fixed_tokens = (
        llm.count_tokens(messages=messages[:truncatable_from], tools=tools).total_tokens  # type: ignore
        if has_system
        else 0
    )
    original_total = total
    for _ in range(_MAX_TRUNCATION_PASSES):
        lengths = [_text_length(m) for m in messages[truncatable_from:]]
        total_chars = sum(lengths)
        if total_chars == 0:
            break
        chars_per_token = total_chars / max(total - fixed_tokens, 1)
        # 10% overshoot so a pass rarely lands just above the budget.
        chars_to_remove = min(total_chars, int((total - budget_tokens) * chars_per_token * 1.1) + 1)
        cap = _water_fill_cap(lengths, chars_to_remove)
        messages = messages[:truncatable_from] + [
            _truncate_message_text(m, cap) for m in messages[truncatable_from:]
        ]
        total = llm.count_tokens(messages=messages, tools=tools).total_tokens  # type: ignore
        if total <= budget_tokens:
            break

    logging.info(
        f"Compaction: truncated the longest messages to fit the summarization request "
        f"({original_total} -> {total} tokens, budget {budget_tokens})"
    )
    return messages, True, total <= budget_tokens


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

    instructions_message = {"role": "user", "content": compaction_instructions}
    truncated_instructions_message = {
        "role": "user",
        "content": f"{compaction_instructions}\n\n{TRUNCATED_INPUT_INSTRUCTION}",
    }
    compaction_usage = RequestStats()

    # The history is compacted because it no longer fits, so the summarization
    # request itself must be cut down to the window: the provider rejects it
    # otherwise and compaction can never recover (ROB-1519). Reserve room for
    # the longer instructions, which are the ones sent once anything is cut.
    truncated_instruction_tokens = llm.count_tokens(
        messages=[truncated_instructions_message]
    ).total_tokens
    input_budget = max(
        0, context_window - maximum_output_token - max(instruction_tokens, truncated_instruction_tokens)
    )
    primary_history, input_truncated, primary_fits = _fit_history_to_token_budget(
        conversation_history, llm, tools, input_budget
    )
    primary_instructions = truncated_instructions_message if input_truncated else instructions_message

    response_message = None
    fallback_reason: Optional[str] = None
    if not primary_fits:
        # A request still over budget would only be rejected by the provider.
        fallback_reason = f"history could not be cut to the {input_budget}-token budget"
    else:
        try:
            if tools:
                response: Optional[ModelResponse] = llm.completion(
                    messages=[*primary_history, primary_instructions],
                    tools=tools,
                    tool_choice="auto",
                    drop_params=True,
                )  # type: ignore
            else:
                response = llm.completion(
                    messages=[*primary_history, primary_instructions], drop_params=True
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
        previous_attempt: Optional[list[dict]] = None
        for budget_fraction in FALLBACK_BUDGET_FRACTIONS:
            fallback_history, truncated, fits = _fit_history_to_token_budget(
                flattened_history, llm, None, int(input_budget * budget_fraction)
            )
            if not fits:
                fallback_reason = f"{fallback_reason}; flattened history could not be cut to the {budget_fraction:.0%} budget"
                break  # a smaller budget cannot fit either
            if fallback_history is previous_attempt:
                break  # a smaller budget no longer changes the request
            previous_attempt = fallback_history
            input_truncated = input_truncated or truncated
            try:
                response = llm.completion(
                    messages=[
                        *fallback_history,
                        truncated_instructions_message if truncated else instructions_message,
                    ],
                    drop_params=True,
                )  # type: ignore
                compaction_usage += RequestStats.from_response(response)
                response_message = _get_response_message(response)
                break
            except Exception as e:
                # Every attempt failed — degrade gracefully via the empty-summary
                # path below (original history returned unchanged) instead of
                # aborting the whole turn.
                fallback_reason = f"{fallback_reason}; fallback request (budget {budget_fraction:.0%}) failed: {e}"
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
            input_truncated=input_truncated,
        )

    compacted_conversation_history: list[dict] = []
    if system_prompt_message:
        compacted_conversation_history.append(system_prompt_message)

    # The summary goes into history as a *user* message built from the response's
    # text only — thinking blocks / provider-specific fields must never be replayed.
    suffix = f"{COMPACTION_TRUNCATION_NOTE}\n\n{COMPACTION_SUMMARY_SUFFIX}" if input_truncated else COMPACTION_SUMMARY_SUFFIX
    compacted_conversation_history.append(
        {
            "role": "user",
            "content": f"{COMPACTION_SUMMARY_PREAMBLE}\n\n{summary_text}\n\n{suffix}",
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
        input_truncated=input_truncated,
    )
