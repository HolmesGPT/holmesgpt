"""
Per-channel delivery conditions for scheduled prompts.

A scheduled prompt's report is, by default, delivered to every notification
channel configured for it (Slack, email). Users may, however, write delivery
conditions directly in their prompt, e.g.:

    "Always post the summary to Slack. Only send the email if there is a
     critical issue that needs human attention."

After the investigation runs, this module asks the LLM to translate those
free-text instructions into a per-sink-type decision map that the reporter
(relay) consumes:

    {"slack": True, "email": False}

Design notes:
- The default is always SEND. A channel is only suppressed when the user's
  prompt specifies a condition for it (or for delivery in general) and the
  report's findings show that condition is not met.
- This is fail-safe: any error (LLM failure, malformed output, empty prompt)
  returns an empty map, which the reporter treats as "deliver everywhere".
- Granularity is per sink *type* (all Slack channels share one decision, all
  email recipients share another), matching the reporter's sink model.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from holmes.core.llm import LLM

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

    def suppressed(self) -> List[str]:
        """Sink types explicitly suppressed (decision is False)."""
        return [sink for sink, send in self.decisions.items() if send is False]

SINK_DECISION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "sink_delivery_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "rationale": {
                    "type": "string",
                    "description": (
                        "Briefly state whether the user's request contained any "
                        "delivery conditions and, if so, whether the report meets them."
                    ),
                },
                "send_to_slack": {
                    "type": "boolean",
                    "description": "Should this report be delivered to Slack?",
                },
                "send_to_email": {
                    "type": "boolean",
                    "description": "Should this report be delivered by email?",
                },
            },
            "required": ["rationale", "send_to_slack", "send_to_email"],
            "additionalProperties": False,
        },
    },
}

SINK_DECISION_SYSTEM_PROMPT = """\
You decide whether a scheduled report should be delivered to each notification \
channel (Slack and email).

The DEFAULT is to SEND the report to every channel. Only suppress a channel when \
the user's request contains an explicit condition about when to send/notify, AND \
the report's findings show that condition is NOT satisfied.

Rules:
- If the user's request says nothing about when or whether to send/notify/alert, \
return send_to_slack=true and send_to_email=true.
- A condition may be global ("only notify me if there is a problem", "skip if \
everything is healthy") — apply it to BOTH channels.
- A condition may be channel-specific ("always post to Slack", "only email me if \
it's critical", "don't email unless action is needed") — apply each condition only \
to the channel it names, and leave the other channel at its default of true.
- Base your decision ONLY on the user's request and the report's findings below. \
Do not invent conditions the user did not state.
- When in doubt, prefer sending (true)."""


def _coerce_decisions(content: str) -> Dict[str, bool]:
    """Parse the LLM's JSON content into a {sink_type: bool} map.

    Only well-formed boolean fields are kept; anything else is ignored so a
    partially-malformed response can never accidentally suppress a channel.
    """
    try:
        parsed: Any = json.loads(content or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    decisions: Dict[str, bool] = {}
    for field, sink in _FIELD_TO_SINK.items():
        value = parsed.get(field)
        if isinstance(value, bool):
            decisions[sink] = value
    return decisions


def _coerce_rationale(content: str) -> str:
    """Best-effort extraction of the model's rationale string."""
    try:
        parsed = json.loads(content or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(parsed, dict) and isinstance(parsed.get("rationale"), str):
        return parsed["rationale"].strip()
    return ""


def evaluate_sink_decisions(
    llm: LLM, prompt_text: str, analysis: str
) -> SinkDecisions:
    """Decide, per sink type, whether to deliver the report.

    Returns a :class:`SinkDecisions`. Empty decisions (on any failure or when
    there is nothing to evaluate) tell the reporter to deliver to every
    configured channel (the safe default).
    """
    if not prompt_text or not prompt_text.strip():
        return SinkDecisions()

    user_content = (
        "Here is the user's scheduled-prompt request:\n"
        f"<request>\n{prompt_text}\n</request>\n\n"
        "Here is the report that was produced:\n"
        f"<report>\n{analysis or ''}\n</report>\n\n"
        "Decide whether to deliver this report to Slack and to email."
    )
    messages = [
        {"role": "system", "content": SINK_DECISION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        result = llm.completion(
            messages=messages,
            response_format=SINK_DECISION_RESPONSE_FORMAT,
            temperature=0,
            drop_params=True,
        )
        content = result.choices[0].message.content  # type: ignore[union-attr]
        return SinkDecisions(
            decisions=_coerce_decisions(content),
            rationale=_coerce_rationale(content),
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
