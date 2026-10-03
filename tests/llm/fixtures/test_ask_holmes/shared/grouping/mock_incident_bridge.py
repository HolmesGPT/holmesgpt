"""Local stdio MCP server that mocks the triager "incident bridge" grouping tools.

This replaces triager's FastAPI-over-SQLite bridge (and, in production, the
Robusta platform / Supabase MCP app) with a self-contained, in-memory
implementation so alert-grouping evals need NO external service.

The tool surface mirrors triager `src/bridge/mcp_server.py`:
  - list_incidents
  - create_incident
  - attach_alerts_to_incident
  - search_pending_in_queue   (look-ahead sibling search)
  - get_queued_alert

State (incidents + the pending queue) lives in this process for the lifetime of
one eval run, so the create -> list -> attach flow behaves like the real bridge.

Seed data is read from a JSON file passed as argv[1]. Shape:

    {
      "leaders": [ {incident dict}, ... ],   # pre-existing OPEN incidents
      "pending": [ {alert dict}, ... ]        # queue for search_pending_in_queue
    }

Both keys are optional. Alerts are otherwise delivered inline in the prompt, so
most scenarios only need "leaders" (and only when testing attach-to-existing).

An optional argv[2] is an OUTPUT path: whenever incidents change, the full
current incident list is written there as JSON. The multi-wave grouping runner
uses this to read the predicted grouping after a run (and to inline current
incidents as leaders on later waves) without needing an MCP round-trip.

Like the real bridge, this rejects any create/attach that fails to supply a
`grouping_reasoning` entry for every alert being placed into an incident
(triager `handlers.py::_validate_reasoning_covers`) — so Holmes is exercised
realistically and a missing reason fails loudly instead of silently passing.
"""

import json
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Incident Bridge (eval mock)")

# ---- in-memory state -------------------------------------------------------

_INCIDENTS: dict[str, dict] = {}
_PENDING: dict[str, dict] = {}
_SEQ = {"n": 0}
_OUT_PATH: str | None = None


def _write_state() -> None:
    if not _OUT_PATH:
        return
    with open(_OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(list(_INCIDENTS.values()), f, default=str, ensure_ascii=False)


def _load_seed() -> None:
    global _OUT_PATH
    if len(sys.argv) > 2:
        _OUT_PATH = sys.argv[2]
    if len(sys.argv) >= 2:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            seed = json.load(f)
        for inc in seed.get("leaders", []) or []:
            inc = dict(inc)
            inc.setdefault("status", "open")
            inc.setdefault("related_alerts", [])
            _INCIDENTS[str(inc["id"])] = inc
        for alert in seed.get("pending", []) or []:
            _PENDING[str(alert["id"])] = dict(alert)
    # Disk-backed state: reload incidents accumulated by earlier tool calls /
    # waves. The MCP stdio server may be re-spawned per call, so in-memory state
    # is NOT assumed to persist — the output file is the source of truth.
    if _OUT_PATH and os.path.exists(_OUT_PATH):
        try:
            with open(_OUT_PATH, "r", encoding="utf-8") as f:
                for inc in json.load(f) or []:
                    _INCIDENTS[str(inc["id"])] = inc
        except (json.JSONDecodeError, OSError):
            pass


def _next_id() -> str:
    highest = 0
    for key in _INCIDENTS:
        if key.startswith("INC-") and key[4:].isdigit():
            highest = max(highest, int(key[4:]))
    return f"INC-{highest + 1:04d}"


def _validate_reasoning_covers(alert_ids: list[str], reasoning: dict[str, str]) -> Optional[str]:
    """Return an error string if any alert lacks a non-empty reason, else None."""
    reasoning = reasoning or {}
    missing = [aid for aid in alert_ids if not (reasoning.get(aid) or "").strip()]
    if missing:
        return (
            "ERROR: grouping_reasoning is required for every alert_id. "
            f"Missing/empty reasons for: {missing}. "
            "Pass grouping_reasoning={alert_id: '<one sentence why it belongs here>'} "
            "covering every alert."
        )
    return None


# ---- tools -----------------------------------------------------------------


@mcp.tool(
    description=(
        "List the currently OPEN incidents (your authoritative grouping leaders). "
        "Call this before creating a new incident to check whether an alert is a "
        "duplicate of an already-open incident that it should be attached to instead."
    )
)
def list_incidents() -> str:
    open_incidents = [inc for inc in _INCIDENTS.values() if inc.get("status", "open") == "open"]
    return json.dumps(open_incidents, default=str, ensure_ascii=False)


@mcp.tool(
    description=(
        "Create a NEW incident from one or more alerts that share a single root "
        "cause. Only call this when the alert(s) are NOT a duplicate of an existing "
        "open incident. `grouping_reasoning` must contain a one-sentence reason for "
        "EVERY alert id in `related_alerts` explaining why it belongs in this incident."
    )
)
def create_incident(
    title: str,
    priority: str,
    related_alerts: list[str],
    grouping_reasoning: dict[str, str],
    affected_resources: Optional[list[str]] = None,
    agents: Optional[list[str]] = None,
    root_cause: Optional[str] = None,
    what_happened: Optional[str] = None,
    how_to_fix: Optional[str] = None,
    urgency_reason: Optional[str] = None,
) -> str:
    err = _validate_reasoning_covers(related_alerts, grouping_reasoning)
    if err:
        return err
    inc_id = _next_id()
    inc = {
        "id": inc_id,
        "title": title,
        "priority": priority,
        "status": "open",
        "related_alerts": list(related_alerts),
        "affected_resources": affected_resources or [],
        "agents": agents or [],
        "root_cause": root_cause,
        "what_happened": what_happened,
        "how_to_fix": how_to_fix,
        "urgency_reason": urgency_reason,
        "grouping_reasoning": dict(grouping_reasoning),
    }
    _INCIDENTS[inc_id] = inc
    _write_state()
    return json.dumps(inc, default=str, ensure_ascii=False)


@mcp.tool(
    description=(
        "Attach one or more alerts to an EXISTING open incident (the cheap "
        "duplicate path — no root-cause write needed). `grouping_reasoning` must "
        "contain a one-sentence reason for every alert id in `alert_ids`."
    )
)
def attach_alerts_to_incident(
    incident_id: str,
    alert_ids: list[str],
    grouping_reasoning: dict[str, str],
) -> str:
    inc = _INCIDENTS.get(str(incident_id))
    if inc is None:
        return (
            f"ERROR: no incident with id '{incident_id}'. "
            f"Known open incidents: {sorted(_INCIDENTS)}"
        )
    err = _validate_reasoning_covers(alert_ids, grouping_reasoning)
    if err:
        return err
    existing = set(inc["related_alerts"])
    for aid in alert_ids:
        if aid not in existing:
            inc["related_alerts"].append(aid)
            existing.add(aid)
    inc.setdefault("grouping_reasoning", {}).update(grouping_reasoning)
    _write_state()
    return json.dumps(inc, default=str, ensure_ascii=False)


@mcp.tool(
    description=(
        "Search the queue of PENDING (not-yet-triaged) alerts for siblings of the "
        "alert you are currently triaging. Filters are AND'd; `q` is a "
        "case-insensitive substring match over title+description. Use it to find "
        "and absorb related alerts into the same incident."
    )
)
def search_pending_in_queue(
    aggregation_key: Optional[str] = None,
    subject_name: Optional[str] = None,
    subject_namespace: Optional[str] = None,
    cluster: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
) -> str:
    results = []
    for alert in _PENDING.values():
        if aggregation_key and alert.get("aggregation_key") != aggregation_key:
            continue
        if subject_name and alert.get("subject_name") != subject_name:
            continue
        if subject_namespace and alert.get("subject_namespace") != subject_namespace:
            continue
        if cluster and alert.get("cluster") != cluster:
            continue
        if q:
            hay = f"{alert.get('title', '')} {alert.get('description', '')}".lower()
            if q.lower() not in hay:
                continue
        results.append(alert)
        if len(results) >= limit:
            break
    return json.dumps(results, default=str, ensure_ascii=False)


@mcp.tool(description="Fetch a single queued alert by id (any status).")
def get_queued_alert(alert_id: str) -> str:
    alert = _PENDING.get(str(alert_id))
    if alert is None:
        return f"ERROR: no queued alert with id '{alert_id}'. Known: {sorted(_PENDING)}"
    return json.dumps(alert, default=str, ensure_ascii=False)


if __name__ == "__main__":
    _load_seed()
    _write_state()  # initialise the output file (empty or seeded leaders)
    mcp.run()
