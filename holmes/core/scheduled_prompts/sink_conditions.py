"""
Per-channel delivery conditions for scheduled prompts.

A scheduled prompt's report is, by default, delivered to every notification
channel configured for it (Slack, email). A user may, however, attach delivery
conditions to the scheduled prompt via a dedicated ``notification_prompt`` field
(stored alongside ``title`` and ``raw_prompt`` in the prompt definition), e.g.:

    "Always post the summary to Slack. Only send the email if there is a
     critical issue that needs human attention."

This is kept separate from the main ``raw_prompt`` on purpose: older Holmes
builds that don't support this feature simply ignore the unknown field, so the
conditions never leak into the investigation prompt.

After the investigation runs, this module asks the LLM to translate those
free-text instructions into a per-sink-type decision map that the reporter
(relay) consumes:

    {"slack": True, "email": False}

Design notes:
- The default is always SEND. A channel is only suppressed when the
  notification instructions specify a condition for it (or for delivery in
  general) and the report's findings show that condition is not met.
- This is fail-safe: any error (LLM failure, malformed output, no instructions)
  returns an empty map, which the reporter treats as "deliver everywhere".
- Granularity is per sink *type* (all Slack channels share one decision, all
  email recipients share another), matching the reporter's sink model. A single
  ``notification_prompt`` drives both channels.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from holmes.common.env_vars import TEMPERATURE
from holmes.core.llm import LLM
from holmes.core.llm_usage import RequestStats

# Maps the structured-output field names to the sink-type keys relay expects.
_FIELD_TO_SINK = {
    "send_to_slack": "slack",
    "send_to_email": "email",
}

# Human-readable label for each sink type, used in the delivery note.
_SINK_LABELS = {
    "slack": "Slack",
    "email": "email",
}


@dataclass
class SinkDecisions:
    """Outcome of evaluating a scheduled prompt's delivery conditions.

    ``decisions`` maps sink type ("slack"/"email") to whether it should be
    delivered. ``rationale`` is the model's brief explanation, surfaced in the
    chat result when a channel is suppressed. An empty ``decisions`` map means
    "deliver to every configured channel" (the safe default).
    """

    decisions: Dict[str, bool] = field(default_factory=dict)
    rationale: str = ""
    # Token/cost stats for the single LLM call this evaluation made, extracted
    # from the completion response. ``None`` means the call never produced a
    # response (it raised) — the caller records that as an error usage event.
    # A non-None (possibly zero) value means the call succeeded.
    stats: Optional[RequestStats] = None

    def suppressed(self) -> List[str]:
        """Sink types explicitly suppressed (decision is False)."""
        return [sink for sink, send in self.decisions.items() if send is False]

SINK_DECISION_SYSTEM_PROMPT = """\
You decide whether a scheduled report should be delivered to each notification \
channel (Slack and email).

You are given the user's NOTIFICATION INSTRUCTIONS and the report that was \
produced. The DEFAULT is to SEND the report to every channel. Only suppress a \
channel when the instructions contain an explicit condition about when to \
send/notify, AND the report's findings show that condition is NOT satisfied.

Rules:
- If the instructions say nothing about when or whether to send/notify/alert, \
return send_to_slack=true and send_to_email=true.
- A condition may be global ("only notify me if there is a problem", "skip if \
everything is healthy") — apply it to BOTH channels.
- A condition may be channel-specific ("always post to Slack", "only email me if \
it's critical", "don't email unless action is needed") — apply each condition only \
to the channel it names, and leave the other channel at its default of true.
- Base your decision ONLY on the notification instructions and the report's \
findings below. Do not invent conditions that were not stated.
- When in doubt, prefer sending (true).

Respond with ONLY a single JSON object and nothing else — no prose, no \
explanation, no markdown, no code fences. The object must have exactly these \
keys:
{"rationale": "<brief reason>", "send_to_slack": <true|false>, "send_to_email": <true|false>}"""


def _extract_json_object(content: str) -> Dict[str, Any]:
    """Best-effort parse of a JSON object from an LLM text response.

    The sink-decision call relies on prompt instructions rather than a strict
    ``response_format`` json_schema, because that schema is rejected (HTTP 400)
    by some providers/proxies (e.g. the Robusta AI proxy). Models therefore may
    wrap the JSON in markdown code fences or surrounding prose, so we tolerate
    both: try a direct parse first, strip ```code fences```, then fall back to
    the first ``{...}`` block. Returns ``{}`` if nothing parseable is found.
    """
    if not content:
        return {}
    text = content.strip()

    # Strip a leading/trailing markdown code fence, e.g. ```json ... ```.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: pull the first {...} span out of surrounding prose.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _coerce_decisions(content: str) -> Dict[str, bool]:
    """Parse the LLM's JSON content into a {sink_type: bool} map.

    Only well-formed boolean fields are kept; anything else is ignored so a
    partially-malformed response can never accidentally suppress a channel.
    """
    parsed = _extract_json_object(content)
    decisions: Dict[str, bool] = {}
    for field_name, sink in _FIELD_TO_SINK.items():
        value = parsed.get(field_name)
        if isinstance(value, bool):
            decisions[sink] = value
    return decisions


def _coerce_rationale(content: str) -> str:
    """Best-effort extraction of the model's rationale string."""
    parsed = _extract_json_object(content)
    rationale = parsed.get("rationale")
    return rationale.strip() if isinstance(rationale, str) else ""


def evaluate_sink_decisions(
    llm: LLM, notification_instructions: str, analysis: str
) -> SinkDecisions:
    """Decide, per sink type, whether to deliver the report.

    ``notification_instructions`` is the user's free-text ``notification_prompt``.
    Returns a :class:`SinkDecisions`. Empty decisions (on any failure or when
    there are no instructions) tell the reporter to deliver to every configured
    channel (the safe default).
    """
    if not notification_instructions or not notification_instructions.strip():
        return SinkDecisions()

    user_content = (
        "Here are the user's notification instructions:\n"
        f"<instructions>\n{notification_instructions}\n</instructions>\n\n"
        "Here is the report that was produced:\n"
        f"<report>\n{analysis or ''}\n</report>\n\n"
        "Decide whether to deliver this report to Slack and to email."
    )
    messages = [
        {"role": "system", "content": SINK_DECISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        # Keep this request shaped like the main investigation chat, which we
        # know the provider/proxy accepts:
        #   - Use the shared TEMPERATURE contract (None on deployments whose
        #     model rejects the parameter — e.g. Anthropic with extended
        #     thinking, which requires temperature unset/1, or Opus 4.7+ which
        #     deprecates it; drop_params can't strip these at the provider).
        #   - Do NOT pass a strict response_format json_schema. Some providers
        #     and the Robusta AI proxy reject it with HTTP 400 (error_code
        #     5400). We instead instruct JSON output in the system prompt and
        #     parse it tolerantly via _extract_json_object.
        result = llm.completion(
            messages=messages,
            temperature=TEMPERATURE,
            drop_params=True,
        )
        content = result.choices[0].message.content  # type: ignore[union-attr]
        decisions = _coerce_decisions(content)
        if not decisions:
            # Successful call but unparseable output: log it so this never fails
            # silently again (deliver-everywhere is still the safe fallback).
            logging.warning(
                "Scheduled-prompt sink delivery evaluation produced no usable "
                "decisions; model output was not parseable as the expected JSON. "
                "Defaulting to deliver to all configured channels. Output: %r",
                (content or "")[:300],
            )
        # Extract token/cost stats for usage tracking. The call succeeded, so
        # always return a (possibly empty) RequestStats — never None — to mark
        # this as a successful LLM call. Guarded so a stats-extraction hiccup
        # can never turn a good decision into a swallowed failure.
        try:
            stats = RequestStats.from_response(result)
        except Exception:
            stats = RequestStats()
        return SinkDecisions(
            decisions=decisions,
            rationale=_coerce_rationale(content),
            stats=stats,
        )
    except Exception:
        logging.exception(
            "Failed to evaluate scheduled-prompt sink delivery conditions; "
            "defaulting to deliver to all configured channels"
        )
        return SinkDecisions()


def format_delivery_note(suppressed: List[str], rationale: str) -> str:
    """Build a short, clearly-delimited note for channels that were withheld.

    Phrased as the delivery *decision* (not actual transport) so it stays
    accurate regardless of which channels the account has configured.
    """
    if not suppressed:
        return ""
    labels = [_SINK_LABELS.get(sink, sink) for sink in suppressed]
    if len(labels) == 1:
        targets = labels[0]
    else:
        targets = f"{', '.join(labels[:-1])} and {labels[-1]}"
    note = (
        f"\n\n---\n_Delivery note: based on the conditions in your prompt, "
        f"this report was not sent to {targets}._"
    )
    if rationale:
        note += f"\n_Reason: {rationale}_"
    return note
