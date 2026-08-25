"""Vendored alert-triage prompts for the grouping evals — two variants.

Variant "triager" (default) is a durable copy of the triage prompt used by the
temporary `triager` project (`src/orchestrator/prompt.py`). Its tie-break bias
is SPLIT: "when in doubt, prefer opening a new incident over grouping".

Variant "production" is a durable copy of the PRODUCTION incident-grouping
prompt that lives in robusta-storage's `incident_grouping_prompt()` SQL RPC
(`db/migrations/20260617094124_incident_grouping_block_rpcs.sql`, itself ported
from relay's INCIDENT_WORKFLOW_SYSTEM_PROMPT, ROB-4011). Its tie-break bias is
the OPPOSITE — MERGE: "grouping repeated or related failures into one incident
is the goal — prefer attaching over opening a new incident". Only tool/field
names are adapted to the eval mocks (get_incidents/search_incidents/
update_incident-attach -> list_incidents/attach_alerts_to_incident; summary/
likely_cause/suggested_fix -> what_happened/root_cause/how_to_fix; the
data-source examples -> the recorded-evidence tools); the guidance and bias
wording are kept verbatim wherever possible so evals measure the real
production behavior.

Both variants are vendored on purpose: the grouping evals must keep working
after the temporary `triager` repo is gone, and must not depend on reading the
robusta-storage database. Re-sync manually if either canonical source changes.

`render_triage_prompt(alerts, leaders=None, variant="triager")` returns a
single prompt string suitable for a HolmesGPT eval `user_prompt`.
"""

from __future__ import annotations

import json

_INVESTIGATION_ORDER = """\
INVESTIGATION ORDER (for each alert in this turn):
1. FIRST decide whether this alert is a duplicate of one of the OPEN INCIDENTS (leaders) below. The leader is always the original — do not compare start times.
   - If YES: call `attach_alerts_to_incident(incident_id, alert_ids, grouping_reasoning={alert_id: "<one sentence>"})`. Done.
   - If NO: go to step 2.
2. Investigate before you decide. The alert text alone is often not enough to know whether two alerts share a root cause. Use the investigation tools — `get_pod_logs`, `kubectl_describe`, `get_resource_events` — to gather evidence (errors, stack traces, correlation/trace ids, events) for the resources involved. Alerts that the evidence shows share a single underlying failure belong in ONE incident; alerts whose evidence shows unrelated causes belong in SEPARATE incidents.
3. Then place every alert: `create_incident(...)` for a new distinct issue, or `attach_alerts_to_incident(...)` for a duplicate of something already open."""

_SHARED_RULES = """\
- A workload-level alert and a pod-level alert about the same workload, or several alert types describing one failure cascade, are the SAME incident. Similar-looking alerts on unrelated workloads, in different namespaces, or firing days apart are DIFFERENT incidents.
- The same resource alerting at clearly different times (e.g. one episode days after another) is NOT one incident — separate episodes are separate incidents even on the same resource.
- Prefer creating a new incident over grouping UNLESS your investigation produced concrete shared evidence (a common root cause, a shared trace/correlation id, one failure clearly causing the other). Over-grouping (false merges) is worse than over-splitting.
- For EVERY alert you place into an incident — via `create_incident` (in `related_alerts`) or `attach_alerts_to_incident` (in `alert_ids`) — you MUST pass a `grouping_reasoning` map keyed by alert_id with a one-sentence reason. Calls that omit a reason for any alert are rejected.
- create_incident fields: title (<80 chars), priority ("urgent"|"not_urgent"|"noise"), related_alerts, grouping_reasoning, and optionally affected_resources, agents, root_cause, what_happened, how_to_fix, urgency_reason.

URGENCY CLASSIFICATION (priority field):
- "urgent": active customer impact, data loss risk, or security exposure.
- "not_urgent": real issue but can wait — non-critical workload, no immediate user impact.
- "noise": no meaningful business impact — false positives, test/staging workloads, known-benign conditions."""

_HEADER = """\
You are an alert triager. Your job: triage and group a set of firing alerts into incidents.

Triage the alerts below. They may belong to one incident, to separate incidents, or attach to an existing open incident. Investigate as needed, then group them correctly.
"""


def _render_leaders_section(leaders: list[dict]) -> str:
    if not leaders:
        return "OPEN INCIDENTS (leaders):\n  (no open incidents yet)"
    lines = ["OPEN INCIDENTS (leaders):"]
    for inc in leaders:
        title = inc.get("title") or ""
        n_alerts = len(inc.get("related_alerts") or [])
        resources = (inc.get("affected_resources") or [])[:3]
        resources_str = ", ".join(resources) if resources else "—"
        lines.append(f"  - {inc.get('id')}  |  \"{title}\"  |  {n_alerts} alerts  |  {resources_str}")
    return "\n".join(lines)


# ---- "production" variant: robusta-storage incident_grouping_prompt() -------
# Kept as close to the SQL RPC text as possible; see module docstring for the
# exact tool/field-name mapping applied.

_PRODUCTION_PROMPT = """\
This investigation is part of a workflow that tracks recurring problems as incidents. Your job is to ATTACH each alert below to an existing incident when it is the same underlying problem, or open a new one — never create a duplicate.

1. Review the OPEN INCIDENTS listed below (most recent first). Decide whether each alert is the same underlying problem as one of them, judging from its summary. This can be true even when the workload, namespace, or source differs (e.g. the same bad dependency or upstream outage breaking several services).
2. If unsure between candidates, call `list_incidents` to read their full detail before deciding.
3. If an alert matches an existing incident, call `attach_alerts_to_incident(incident_id, alert_ids, grouping_reasoning={alert_id: "<why this is the same problem>"})`. Refine the incident via `update_incident` if this alert broadens its scope or you learned something new about the root cause — rewrite `what_happened` as a tight gist that conveys the breadth qualitatively; do NOT enumerate every workload, so it stays about the same length as the incident grows.
4. Only if it is genuinely a new problem, investigate the root cause and call `create_incident` with a clear title, priority, what_happened, root_cause, how_to_fix and the affected resources — plus `related_alerts` and a `grouping_reasoning` entry for every alert placed in it.

Grouping repeated or related failures into one incident is the goal — prefer attaching over opening a new incident.

Investigate before you conclude: the alerts point at concrete workloads, and matching data-source tools are available to you (`get_pod_logs`, `kubectl_describe`, `get_resource_events`) — QUERY them to find the actual root cause and cite the concrete evidence you find; do not guess the cause from the alert text alone.

Formatting: write `what_happened`, `root_cause` and `how_to_fix` as plain Markdown. When you use a numbered or bulleted list, put each item on its OWN line separated by a newline — never inline on a single line, which the UI cannot render as a list."""

_VARIANTS = ("triager", "production")


def render_triage_prompt(
    alerts: list[dict],
    leaders: list[dict] | None = None,
    variant: str = "triager",
) -> str:
    if not alerts:
        raise ValueError("render_triage_prompt requires at least one alert")
    if variant not in _VARIANTS:
        raise ValueError(f"unknown prompt variant: {variant} (valid: {_VARIANTS})")
    if variant == "production":
        parts = [_PRODUCTION_PROMPT, ""]
    else:
        parts = [
            _HEADER,
            _INVESTIGATION_ORDER,
            "",
            "Rules:",
            _SHARED_RULES,
            "",
        ]
    parts.append(_render_leaders_section(leaders or []))
    parts.append("")
    parts.append("ALERTS:")
    for alert in alerts:
        parts.append(json.dumps(alert, default=str, ensure_ascii=False))
    return "\n".join(parts)
