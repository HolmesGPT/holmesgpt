"""Local stdio MCP server that serves RECORDED root-cause-analysis evidence.

This is the "model" the alert-grouping evals investigate against: pre-generated,
per-resource evidence (pod logs, `kubectl describe`, events) that Holmes pulls
via real tool calls — exactly like it would against a live cluster, but sourced
from a fixture file instead. No live Kubernetes, no Supabase, fully
reproducible, and scales to any number of alerts.

Correct grouping in these evals is designed to be DISCOVERABLE ONLY BY
INVESTIGATION: e.g. two superficially-different alerts share a hidden trace-id
that appears only in their logs, so a model that groups from alert text alone
(without pulling logs) gets it wrong. Baking unique codes into the evidence is
the anti-hallucination lever (same idea as the Elasticsearch evals' injected
ERR-XXXX codes).

Seed data is read from a JSON file passed as argv[1]. Shape:

    {
      "evidence": {
        "logs":     { "<namespace>/<pod>":        "…log text…" },
        "describe": { "<kind>/<namespace>/<name>": "…describe text…" },
        "events":   { "<namespace>/<name>":        "…events text…" }
      }
    }

Every tool returns a detailed, self-correcting error (listing the keys that DO
exist) when asked for a resource with no recorded evidence — per HolmesGPT's
"toolsets must return actionable errors" rule.
"""

import json
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Cluster RCA Evidence (recorded)")

_EVIDENCE: dict[str, dict[str, str]] = {"logs": {}, "describe": {}, "events": {}}


def _load_seed() -> None:
    if len(sys.argv) < 2:
        return
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        seed = json.load(f)
    ev = seed.get("evidence", {}) or {}
    for section in ("logs", "describe", "events"):
        _EVIDENCE[section].update(ev.get(section, {}) or {})


def _lookup(section: str, key: str, *fallbacks: str) -> str:
    table = _EVIDENCE[section]
    for candidate in (key, *fallbacks):
        if candidate in table:
            return table[candidate]
    # forgiving: unique suffix match (e.g. pod name only)
    suffix_hits = [k for k in table if k.endswith("/" + key) or k == key]
    if len(suffix_hits) == 1:
        return table[suffix_hits[0]]
    available = sorted(table)
    return (
        f"ERROR: no recorded {section} for '{key}'. "
        f"Available {section} keys: {available}"
    )


@mcp.tool(
    description=(
        "Fetch recent logs for a pod. Provide the pod's namespace and name. Use "
        "this to inspect what a workload was actually doing around the time an "
        "alert fired — error messages, stack traces, and correlation ids."
    )
)
def get_pod_logs(namespace: str, pod_name: str) -> str:
    return _lookup("logs", f"{namespace}/{pod_name}", pod_name)


@mcp.tool(
    description=(
        "Describe a Kubernetes resource (like `kubectl describe`). Provide kind "
        "(e.g. Deployment, Pod, Node), namespace, and name. Returns status, "
        "conditions, recent state transitions, and related metadata."
    )
)
def kubectl_describe(kind: str, namespace: str, name: str) -> str:
    return _lookup("describe", f"{kind}/{namespace}/{name}", f"{namespace}/{name}", name)


@mcp.tool(
    description=(
        "Fetch the Kubernetes events for a resource (like `kubectl get events`). "
        "Provide namespace and the resource name. Returns the chronological event "
        "stream (scheduling, restarts, probe failures, evictions, etc.)."
    )
)
def get_resource_events(namespace: str, name: str) -> str:
    return _lookup("events", f"{namespace}/{name}", name)


if __name__ == "__main__":
    _load_seed()
    mcp.run()
