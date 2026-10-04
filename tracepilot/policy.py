"""Deterministic eligibility rules shared by planning and execution."""

from typing import Any

from tracepilot.errors import TRANSIENT_TOOL_ERRORS
from tracepilot.models import Candidate, DecisionState, ToolSpec


CONTROL_CHOICES = {
    "reason": "Ask the LLM to analyze evidence and prepare new tool arguments.",
    "finish": "End the investigation and produce a report, which may be inconclusive. This never confirms or fixes a root cause.",
    "need_input": "Ask the operator for missing scope, permission or information that tools cannot obtain.",
}


def can_attempt(state: DecisionState, candidate: Candidate, spec: ToolSpec) -> bool:
    if state.tool_calls >= state.budget.max_tool_calls or candidate.tool_version != spec.version:
        return False
    attempts = [record for record in state.history if record.fingerprint == candidate.fingerprint]
    if not attempts:
        return True
    last = attempts[-1]
    return (
        len(attempts) < state.budget.max_tool_attempts
        and spec.retry_safe and last.status == "error"
        and not last.retry_exhausted
        and last.error_kind in TRANSIENT_TOOL_ERRORS
    )


def can_reason(state: DecisionState) -> bool:
    return (state.reasoning_calls < state.budget.max_reasoning_calls
            and state.no_progress < state.budget.max_no_progress)


def available_actions(state: DecisionState) -> dict[str, Any]:
    actions: dict[str, Any] = {name: description for name, description in CONTROL_CHOICES.items()
                               if name != "reason" or can_reason(state)}
    catalog = {spec.name: spec for spec in state.available_tools}
    for candidate in state.candidates:
        spec = catalog.get(candidate.tool)
        if spec is not None and can_attempt(state, candidate, spec):
            actions[f"call:{candidate.id}"] = {
                "tool": candidate.tool, "purpose": candidate.purpose,
                "direction": candidate.direction, "arguments": candidate.arguments,
            }
    return actions
