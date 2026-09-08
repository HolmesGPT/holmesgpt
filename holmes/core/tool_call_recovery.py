"""Recover tool calls that a model emitted as literal XML text.

Some models (Claude in particular) sometimes "narrate" a tool call in the
Anthropic tool-use XML dialect they were trained on::

    <function_calls>
    <invoke name="update_ai_triage_metadata">
    <parameter name="team">payments</parameter>
    <parameter name="root_cause_analysis"># RCA ...</parameter>
    </invoke>
    </function_calls>

instead of returning it as a structured ``tool_calls`` field. This happens on
routes where native (structured) function calling is not used end-to-end — e.g.
LiteLLM's prompt-based tool-calling fallback for models it doesn't recognise as
function-calling-capable. LiteLLM is supposed to parse that XML back into
``tool_calls`` itself, but its parser (``ET.fromstring``) is strict XML and
breaks whenever a parameter value contains markdown / unescaped ``<``, ``>``,
``&`` or code fences, so the raw XML leaks into ``message.content`` and no tool
call is made.

When that happens Holmes treats the XML as the final answer: the intended tool
(e.g. relay's ``update_ai_triage_metadata``) never runs, and the raw tags show
up in the user-facing output (ROB-558).

This module recovers such calls with a lenient, regex-based parser (never
``ET.fromstring``) so a markdown-laden parameter value can't defeat it, and only
when the recovered tool name was actually offered to the model — so ordinary
prose that merely mentions ``<invoke>`` is never misread as a tool call.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from litellm.types.utils import ChatCompletionMessageToolCall, Function

# The whole call container and each invoke block. `name="..."` is the modern
# Anthropic dialect; the older LiteLLM/Anthropic template used a nested
# `<tool_name>...</tool_name>` instead — both are handled.
_INVOKE_OPEN_RE = re.compile(
    r"<invoke\b[^>]*?\bname\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
_INVOKE_BARE_OPEN_RE = re.compile(r"<invoke\b[^>]*>", re.IGNORECASE)
_INVOKE_CLOSE_RE = re.compile(r"</invoke>", re.IGNORECASE)
_TOOL_NAME_TAG_RE = re.compile(
    r"<tool_name>\s*(.+?)\s*</tool_name>", re.IGNORECASE | re.DOTALL
)
# A single `<parameter name="key">` opening marker. We deliberately do NOT try
# to match its closing tag: the model closes it inconsistently (`</parameter>`,
# `</key>`, or not at all), so each value is delimited by the NEXT marker /
# block boundary and trailing close tags are stripped afterwards.
_PARAM_OPEN_RE = re.compile(
    r"<parameter\b[^>]*?\bname\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
# Trailing close tag left on a parameter value once it's been sliced out.
_TRAILING_CLOSE_RE = re.compile(
    r"\s*</(?:parameter|parameters|invoke|function_calls|[A-Za-z0-9_.\-]+)>\s*$",
    re.IGNORECASE,
)
_FUNCTION_CALLS_OPEN_RE = re.compile(r"<function_calls>", re.IGNORECASE)


def _looks_like_tool_call_xml(content: str) -> bool:
    """Cheap pre-check so we never run the parser on ordinary answers."""
    return "<invoke" in content.lower()


def _maybe_json(value: str) -> Any:
    """Mirror LiteLLM's parse_xml_params: decode a value as JSON when it looks
    like a JSON scalar/array/object, otherwise keep the raw (stripped) string."""
    stripped = value.strip()
    if stripped and stripped[0] in "[{" or stripped in ("true", "false", "null"):
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            return value
    return value


def _parse_parameters(block: str) -> Dict[str, Any]:
    """Extract `<parameter name="k">v</parameter>`-style params from an invoke
    block. Each value runs from its opening marker to the next marker (or the
    end of the block); the trailing close tag, however the model wrote it, is
    stripped."""
    markers = list(_PARAM_OPEN_RE.finditer(block))
    params: Dict[str, Any] = {}
    for idx, match in enumerate(markers):
        key = match.group(1)
        start = match.end()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(block)
        raw = block[start:end]
        raw = _TRAILING_CLOSE_RE.sub("", raw)
        params[key] = _maybe_json(raw)
    return params


def _recover_invocations(
    content: str, offered_tool_names: Set[str]
) -> List[Tuple[str, Dict[str, Any], int, int]]:
    """Return (tool_name, params, span_start, span_end) for each invoke block
    whose tool name was actually offered to the model."""
    recovered: List[Tuple[str, Dict[str, Any], int, int]] = []
    pos = 0
    lowered_len = len(content)
    while pos < lowered_len:
        named = _INVOKE_OPEN_RE.search(content, pos)
        bare = _INVOKE_BARE_OPEN_RE.search(content, pos)
        if named is None and bare is None:
            break
        # Use whichever `<invoke ...>` comes first.
        open_match = min(
            (m for m in (named, bare) if m is not None), key=lambda m: m.start()
        )
        body_start = open_match.end()
        close_match = _INVOKE_CLOSE_RE.search(content, body_start)
        body_end = close_match.start() if close_match else len(content)
        span_end = close_match.end() if close_match else len(content)
        block = content[body_start:body_end]

        tool_name: Optional[str] = None
        if open_match is named:
            tool_name = open_match.group(1)
        else:
            name_tag = _TOOL_NAME_TAG_RE.search(block)
            if name_tag:
                tool_name = name_tag.group(1).strip()

        if tool_name and tool_name in offered_tool_names:
            params = _parse_parameters(block)
            span_start = open_match.start()
            # Absorb a leading `<function_calls>` wrapper into the removed span
            # so it doesn't linger in the cleaned content.
            wrapper = _FUNCTION_CALLS_OPEN_RE.search(
                content, max(0, span_start - 40), span_start
            )
            if wrapper is not None and content[wrapper.end() : span_start].strip() == "":
                span_start = wrapper.start()
            recovered.append((tool_name, params, span_start, span_end))

        pos = span_end if span_end > pos else pos + 1
    return recovered


def _strip_spans(content: str, spans: List[Tuple[int, int]]) -> str:
    """Remove the recovered XML spans and tidy up leftover `<function_calls>`
    scaffolding and whitespace."""
    kept = []
    cursor = 0
    for start, end in sorted(spans):
        kept.append(content[cursor:start])
        cursor = max(cursor, end)
    kept.append(content[cursor:])
    cleaned = "".join(kept)
    # Drop now-empty container tags left behind.
    cleaned = re.sub(
        r"</?function_calls>", "", cleaned, flags=re.IGNORECASE
    )
    return cleaned.strip()


def recover_tool_calls_from_text(
    content: Optional[str], offered_tool_names: Set[str]
) -> Tuple[Optional[str], List[ChatCompletionMessageToolCall]]:
    """Recover tool calls a model wrote as XML text instead of structured
    ``tool_calls``.

    Returns ``(cleaned_content, tool_calls)``. When nothing is recovered the
    original ``content`` is returned unchanged and the list is empty.
    """
    if not content or not offered_tool_names or not _looks_like_tool_call_xml(content):
        return content, []

    invocations = _recover_invocations(content, offered_tool_names)
    if not invocations:
        return content, []

    tool_calls: List[ChatCompletionMessageToolCall] = []
    spans: List[Tuple[int, int]] = []
    for tool_name, params, span_start, span_end in invocations:
        tool_calls.append(
            ChatCompletionMessageToolCall(
                id=f"call_recovered_{uuid.uuid4().hex[:24]}",
                type="function",
                function=Function(name=tool_name, arguments=json.dumps(params)),
            )
        )
        spans.append((span_start, span_end))

    cleaned = _strip_spans(content, spans)
    logging.warning(
        "Recovered %d tool call(s) emitted as XML text in the model response "
        "(tools: %s). The model narrated the call instead of returning a "
        "structured tool_calls field; recovering it so the tool runs.",
        len(tool_calls),
        ", ".join(tc.function.name for tc in tool_calls),
    )
    return (cleaned or None), tool_calls
