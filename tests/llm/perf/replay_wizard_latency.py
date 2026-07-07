#!/usr/bin/env python3
"""Replay the "Add data source" chat-wizard request against a Holmes relay and
measure latency from the streamed SSE event timeline.

This is a measurement harness, NOT a pytest eval. The tests/llm/ eval framework
runs Holmes in-process; this instead replays the exact request the Robusta UI
sends (POST /integrations/stream/actions, action_name=holmes_chat) against a
*deployed* Holmes (e.g. staging in the DigitalOcean cluster), so the numbers
reflect the real model, config, and cluster egress.

The wizard request body is captured once from the live UI (see README.md) and
stored as a fixture with the session_token stripped. The token is injected here
at run time from the environment; nothing secret is committed.

What it measures, per run and aggregated over N runs:
  - total wall time (request sent -> ai_answer_end)
  - time to first event
  - number of LLM turns (tool-call batches) and total tool calls
  - per tool call: name, the invocation description (fetch_webpage carries its
    URL here), and duration (start_tool_calling -> matching tool_calling_result)
  - tokens / cost if the deployment reports them in the final metadata

Usage:
  ROBUSTA_STAGING_SESSION_TOKEN=<jwt> \
  poetry run python tests/llm/perf/replay_wizard_latency.py \
      --request tests/llm/perf/fixtures/mcp_wizard_request.json \
      --iterations 5 \
      --out tests/llm/perf/baseline

Braintrust (optional, only if the deployment has HOLMES_ALLOW_PER_REQUEST_EXPERIMENT=true):
  add --braintrust-experiment "mcp-wizard-baseline" to group all spans under one
  experiment in the robustadev project.
"""

import argparse
import copy
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

DEFAULT_URL = "https://stg.api.robusta.dev/integrations/stream/actions"
# ai_answer_end is the terminal event; error / rate-limit end the run too.
TERMINAL_EVENTS = {"ai_answer_end", "error", "approval_required"}


@dataclass
class ToolCall:
    tool_name: str
    tool_id: Optional[str]
    description: Optional[str] = None
    start_ts: Optional[float] = None
    result_ts: Optional[float] = None
    status: Optional[str] = None

    @property
    def duration(self) -> Optional[float]:
        if self.start_ts is not None and self.result_ts is not None:
            return self.result_ts - self.start_ts
        return None


@dataclass
class RunResult:
    ok: bool
    total_wall: float
    time_to_first_event: Optional[float]
    turns: int  # number of tool-call batches (a proxy for LLM iterations that called tools)
    tool_calls: List[ToolCall] = field(default_factory=list)
    event_counts: Dict[str, int] = field(default_factory=dict)
    answer_chars: int = 0
    produced_setup_guide: bool = False
    tokens: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _iter_sse_events(response: requests.Response):
    """Yield (event_type, data_str) tuples from an SSE stream as they arrive."""
    event_type = None
    data_lines: List[str] = []
    for raw in response.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw
        if line == "":
            # blank line terminates an event
            if event_type is not None or data_lines:
                yield event_type or "message", "\n".join(data_lines)
            event_type = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue  # comment / heartbeat
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    # flush trailing event with no terminating newline
    if event_type is not None or data_lines:
        yield event_type or "message", "\n".join(data_lines)


def build_body(template: Dict[str, Any], session_token: str) -> Dict[str, Any]:
    """Return a fresh request body: inject the token, refresh timestamp and a new
    conversation id so each run is a clean conversation."""
    body = copy.deepcopy(template)
    body["session_token"] = session_token
    inner = body.setdefault("body", {})
    inner["timestamp"] = int(time.time())
    params = inner.setdefault("action_params", {})
    params["conversation_id"] = str(uuid.uuid4())
    params["stream"] = True
    return body


def run_once(
    url: str,
    template: Dict[str, Any],
    session_token: str,
    braintrust_experiment: Optional[str],
    timeout: float,
    verbose: bool,
) -> RunResult:
    body = build_body(template, session_token)
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if braintrust_experiment:
        headers["X-Braintrust-Experiment"] = braintrust_experiment

    tool_by_id: Dict[str, ToolCall] = {}
    tool_calls: List[ToolCall] = []
    event_counts: Dict[str, int] = {}
    turns = 0
    prev_event: Optional[str] = None
    answer_chars = 0
    produced_setup_guide = False
    tokens: Dict[str, Any] = {}
    first_event_ts: Optional[float] = None

    t0 = time.time()
    try:
        with requests.post(
            url, json=body, headers=headers, stream=True, timeout=timeout
        ) as resp:
            if resp.status_code >= 400:
                snippet = resp.text[:500]
                return RunResult(
                    ok=False,
                    total_wall=time.time() - t0,
                    time_to_first_event=None,
                    turns=0,
                    error=f"HTTP {resp.status_code}: {snippet}",
                )
            for event_type, data_str in _iter_sse_events(resp):
                now = time.time()
                if first_event_ts is None:
                    first_event_ts = now
                event_counts[event_type] = event_counts.get(event_type, 0) + 1

                data: Dict[str, Any] = {}
                if data_str:
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = {}

                if event_type == "start_tool_calling":
                    # a new tool batch = a new turn (first start, or a start that
                    # follows a non-start event)
                    if prev_event != "start_tool_calling":
                        turns += 1
                    tc = ToolCall(
                        tool_name=data.get("tool_name", "?"),
                        tool_id=data.get("id"),
                        start_ts=now,
                    )
                    tool_calls.append(tc)
                    if tc.tool_id:
                        tool_by_id[tc.tool_id] = tc
                    if verbose:
                        print(f"    [{now - t0:6.2f}s] -> {tc.tool_name}")
                elif event_type == "tool_calling_result":
                    tid = data.get("tool_call_id") or data.get("id")
                    name = data.get("tool_name") or data.get("name") or "?"
                    desc = data.get("description")
                    status = None
                    result = data.get("result")
                    if isinstance(result, dict):
                        status = result.get("status")
                    tc = tool_by_id.get(tid) if tid else None
                    if tc is None:
                        tc = ToolCall(tool_name=name, tool_id=tid)
                        tool_calls.append(tc)
                    tc.result_ts = now
                    tc.description = desc
                    tc.status = status
                    if name == "DataSourceSetupGuide":
                        produced_setup_guide = True
                    if verbose:
                        dur = tc.duration
                        durs = f"{dur:.2f}s" if dur is not None else "?"
                        print(f"    [{now - t0:6.2f}s] <- {name} ({durs}) {desc or ''}")
                elif event_type in ("token_count", "ai_answer_end"):
                    meta = data.get("metadata") or {}
                    if isinstance(meta, dict):
                        for k in ("usage", "tokens", "max_tokens", "max_output_tokens"):
                            if k in meta:
                                tokens[k] = meta[k]
                    if event_type == "ai_answer_end":
                        analysis = data.get("analysis") or ""
                        answer_chars = len(analysis)
                elif event_type == "error":
                    return RunResult(
                        ok=False,
                        total_wall=time.time() - t0,
                        time_to_first_event=(first_event_ts - t0) if first_event_ts else None,
                        turns=turns,
                        tool_calls=tool_calls,
                        event_counts=event_counts,
                        error=f"stream error event: {data_str[:400]}",
                    )

                prev_event = event_type
                if event_type in TERMINAL_EVENTS:
                    break
    except requests.RequestException as e:
        return RunResult(
            ok=False,
            total_wall=time.time() - t0,
            time_to_first_event=(first_event_ts - t0) if first_event_ts else None,
            turns=turns,
            tool_calls=tool_calls,
            event_counts=event_counts,
            error=f"request failed: {e}",
        )

    return RunResult(
        ok=True,
        total_wall=time.time() - t0,
        time_to_first_event=(first_event_ts - t0) if first_event_ts else None,
        turns=turns,
        tool_calls=tool_calls,
        event_counts=event_counts,
        answer_chars=answer_chars,
        produced_setup_guide=produced_setup_guide,
        tokens=tokens,
    )


def _fmt(v: Optional[float]) -> str:
    return f"{v:.2f}s" if v is not None else "n/a"


def _stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def summarize(runs: List[RunResult]) -> Dict[str, Any]:
    ok_runs = [r for r in runs if r.ok]
    totals = [r.total_wall for r in ok_runs]
    ttfe = [r.time_to_first_event for r in ok_runs if r.time_to_first_event is not None]
    turns = [r.turns for r in ok_runs]
    tool_counts = [len(r.tool_calls) for r in ok_runs]

    # per-tool-name aggregate duration across all runs
    per_tool: Dict[str, List[float]] = {}
    fetch_urls: List[str] = []
    for r in ok_runs:
        for tc in r.tool_calls:
            if tc.duration is not None:
                per_tool.setdefault(tc.tool_name, []).append(tc.duration)
            if tc.tool_name == "fetch_webpage" and tc.description:
                fetch_urls.append(tc.description)

    return {
        "runs": len(runs),
        "ok": len(ok_runs),
        "failed": len(runs) - len(ok_runs),
        "total_wall": _stats(totals),
        "time_to_first_event": _stats([float(x) for x in ttfe]),
        "turns": _stats([float(x) for x in turns]),
        "tool_calls_per_run": _stats([float(x) for x in tool_counts]),
        "per_tool_duration": {
            name: {"count": len(v), **_stats(v)} for name, v in sorted(per_tool.items())
        },
        "fetch_webpage_invocations": sorted(set(fetch_urls)),
    }


def render_markdown(summary: Dict[str, Any], runs: List[RunResult], meta: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# MCP data-source wizard — latency baseline\n")
    lines.append(f"- Endpoint: `{meta['url']}`")
    lines.append(f"- Model: `{meta.get('model', 'unknown')}`")
    lines.append(f"- Iterations: {summary['runs']} (ok: {summary['ok']}, failed: {summary['failed']})")
    if meta.get("braintrust_experiment"):
        lines.append(f"- Braintrust experiment: `{meta['braintrust_experiment']}`")
    lines.append("")
    tw = summary["total_wall"]
    if tw:
        lines.append("## End-to-end wall time")
        lines.append(f"- mean **{tw['mean']:.1f}s**, median {tw['median']:.1f}s, min {tw['min']:.1f}s, max {tw['max']:.1f}s")
        lines.append("")
    tt = summary["time_to_first_event"]
    if tt:
        lines.append(f"- time to first event: mean {tt['mean']:.2f}s")
    tn = summary["turns"]
    if tn:
        lines.append(f"- LLM turns (tool batches): mean {tn['mean']:.1f}, max {int(tn['max'])}")
    tc = summary["tool_calls_per_run"]
    if tc:
        lines.append(f"- tool calls per run: mean {tc['mean']:.1f}, max {int(tc['max'])}")
    lines.append("")
    if summary["per_tool_duration"]:
        lines.append("## Per-tool duration (across all runs)")
        lines.append("")
        lines.append("| tool | calls | mean | median | max |")
        lines.append("|---|---|---|---|---|")
        for name, s in summary["per_tool_duration"].items():
            lines.append(f"| `{name}` | {s['count']} | {s['mean']:.2f}s | {s['median']:.2f}s | {s['max']:.2f}s |")
        lines.append("")
    if summary["fetch_webpage_invocations"]:
        lines.append("## fetch_webpage invocations observed")
        lines.append("")
        for inv in summary["fetch_webpage_invocations"]:
            lines.append(f"- {inv}")
        lines.append("")
    lines.append("## Per-run detail")
    lines.append("")
    for i, r in enumerate(runs, 1):
        if not r.ok:
            lines.append(f"- run {i}: FAILED — {r.error}")
            continue
        guide = "guide✓" if r.produced_setup_guide else "guide✗"
        lines.append(
            f"- run {i}: {r.total_wall:.1f}s, {r.turns} turns, {len(r.tool_calls)} tools, {guide}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--request", required=True, help="Path to captured request body JSON (token stripped).")
    ap.add_argument("--url", default=os.environ.get("ROBUSTA_RELAY_URL", DEFAULT_URL))
    ap.add_argument("--iterations", type=int, default=int(os.environ.get("ITERATIONS", "5")))
    ap.add_argument("--out", default="tests/llm/perf/baseline", help="Output dir for report + json.")
    ap.add_argument("--braintrust-experiment", default=os.environ.get("BRAINTRUST_EXPERIMENT"))
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("REPLAY_TIMEOUT", "600")))
    ap.add_argument("--verbose", action="store_true", default=os.environ.get("VERBOSE") == "1")
    args = ap.parse_args()

    token = os.environ.get("ROBUSTA_STAGING_SESSION_TOKEN")
    if not token:
        print("ERROR: set ROBUSTA_STAGING_SESSION_TOKEN (the staging session JWT).", file=sys.stderr)
        return 2

    with open(args.request, "r", encoding="utf-8") as f:
        template = json.load(f)
    if template.get("session_token"):
        print("WARNING: request fixture contains a session_token; it will be overwritten "
              "and should be stripped before committing.", file=sys.stderr)

    model = (template.get("body", {}).get("action_params", {}) or {}).get("model", "unknown")

    print(f"Replaying {args.iterations}x against {args.url} (model={model})")
    runs: List[RunResult] = []
    for i in range(1, args.iterations + 1):
        print(f"--- run {i}/{args.iterations} ---")
        r = run_once(args.url, template, token, args.braintrust_experiment, args.timeout, args.verbose)
        status = "ok" if r.ok else f"FAILED ({r.error})"
        print(f"    {status}: {_fmt(r.total_wall)} total, ttfe {_fmt(r.time_to_first_event)}, "
              f"{r.turns} turns, {len(r.tool_calls)} tools, guide={'yes' if r.produced_setup_guide else 'no'}")
        runs.append(r)

    summary = summarize(runs)
    meta = {"url": args.url, "model": model, "braintrust_experiment": args.braintrust_experiment}

    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "latency_baseline.json")
    md_path = os.path.join(args.out, "latency_baseline.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": meta,
                "summary": summary,
                "runs": [
                    {
                        "ok": r.ok,
                        "total_wall": r.total_wall,
                        "time_to_first_event": r.time_to_first_event,
                        "turns": r.turns,
                        "tool_calls": [
                            {
                                "tool_name": tc.tool_name,
                                "description": tc.description,
                                "duration": tc.duration,
                                "status": tc.status,
                            }
                            for tc in r.tool_calls
                        ],
                        "event_counts": r.event_counts,
                        "answer_chars": r.answer_chars,
                        "produced_setup_guide": r.produced_setup_guide,
                        "tokens": r.tokens,
                        "error": r.error,
                    }
                    for r in runs
                ],
            },
            f,
            indent=2,
        )
    md = render_markdown(summary, runs, meta)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print("\n" + md)
    print(f"\nWrote {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
