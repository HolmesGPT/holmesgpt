"""Vendored alert-triage prompt for the grouping evals.

This is a durable copy of the triage prompt used by the `triager` project
(`src/orchestrator/prompt.py`), adapted to the MCP tool names exposed by the
eval mocks in this directory (`create_incident`, `attach_alerts_to_incident`,
`list_incidents`, `search_pending_in_queue`) and to the recorded RCA-evidence
tools (`get_pod_logs`, `kubectl_describe`, `get_resource_events`).

It is vendored on purpose: the grouping evals must keep working after the
temporary `triager` repo is gone. If the canonical triager prompt changes and
we want parity, re-sync from triager `render_system_prompt` /
`render_user_message` and re-render the fixtures with `build_scenarios.py`.

`render_triage_prompt(alerts, leaders=None)` returns a single prompt string
suitable for a HolmesGPT eval `user_prompt`.
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


def render_triage_prompt(alerts: list[dict], leaders: list[dict] | None = None) -> str:
    if not alerts:
        raise ValueError("render_triage_prompt requires at least one alert")
    parts = [
        _HEADER,
        _INVESTIGATION_ORDER,
        "",
        "Rules:",
        _SHARED_RULES,
        "",
        _render_leaders_section(leaders or []),
        "",
        "ALERTS:",
    ]
    for alert in alerts:
        parts.append(json.dumps(alert, default=str, ensure_ascii=False))
    return "\n".join(parts)
