"""At-scale, multi-wave alert-grouping runner (ROB-517 DoD #2/#3).

This is the durable, self-contained replacement for the temporary `triager`
orchestrator. It drives the REAL Holmes engine (`ToolCallingLLM`) over a dataset
of alerts, grouping them into incidents via the local mock incident bridge, with
the recorded RCA-evidence mock as the investigation "model" — no Supabase, no
MCP app, no live cluster.

Two things the single-turn pytest eval harness cannot express, and why they live
here instead:
  * WAVES — alerts are replayed in time order, in batches ("waves"). Incident
    state persists across waves (same tool executor / same MCP process), and each
    wave sees the incidents opened by earlier waves inlined as leaders. This is
    what makes streamed/temporal grouping testable (a day-2 alert must not attach
    to a day-1 incident).
  * GROUPING OFF vs ON — an OFF baseline (one incident per alert, no LLM) to
    compare against Holmes' ON grouping, plus quantitative accuracy + cost +
    speed metrics.

Dataset JSON shape (`--dataset path/to/dataset.json`):
    {
      "alerts":       [ {alert dict with id, starts_at, ...}, ... ],
      "evidence":     { "logs": {...}, "describe": {...}, "events": {...} },
      "ground_truth": { "<alert_id>": "<incident_key>", ... },
      "pending":      [ ... ]   # optional, for look-ahead sibling search
    }

Usage (from the holmesgpt repo root, with the eval LLM env set — see CLAUDE.md):
    poetry run python tests/llm/fixtures/test_ask_holmes/shared/grouping/grouping_runner.py \
        --dataset <dataset.json> --mode on --wave-size all --model opus-4.6
    # OFF baseline needs no model / no API calls:
    poetry run python .../grouping_runner.py --dataset <dataset.json> --mode off
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring import (  # noqa: E402
    cost_speed,
    format_summary,
    incidents_to_partition,
    score_all,
)
from triage_prompt import render_triage_prompt  # noqa: E402

SHARED_REL = "tests/llm/fixtures/test_ask_holmes/shared/grouping"


def _sorted_by_time(alerts: list[dict]) -> list[dict]:
    return sorted(alerts, key=lambda a: (a.get("starts_at") or "", str(a.get("id"))))


def _make_waves(alerts: list[dict], wave_size: str) -> list[list[dict]]:
    alerts = _sorted_by_time(alerts)
    if wave_size == "all":
        return [alerts]
    n = int(wave_size)
    return [alerts[i : i + n] for i in range(0, len(alerts), n)]


# --------------------------------------------------------------------------- #
# GROUPING OFF baseline — one incident per alert, no LLM, no cost.
# --------------------------------------------------------------------------- #
def run_off(dataset: dict) -> dict:
    alerts = dataset["alerts"]
    predicted = {str(a["id"]): f"solo-{a['id']}" for a in alerts}
    truth = {str(k): str(v) for k, v in dataset["ground_truth"].items()}
    scores = score_all(predicted, truth)
    scores.update(cost_speed(0.0, 0.0, len(alerts)))
    scores["n_incidents"] = len(predicted)
    return scores


# --------------------------------------------------------------------------- #
# GROUPING ON — Holmes triages the alerts wave by wave.
# --------------------------------------------------------------------------- #
def _write_run_config(workdir: Path, dataset: dict) -> tuple[Path, Path]:
    """Write the seed (evidence + pending) and a toolsets.yaml wiring both mocks;
    return (toolsets_yaml_path, incidents_out_path)."""
    seed_path = workdir / "seed.json"
    out_path = workdir / "incidents_out.json"
    seed = {
        "leaders": [],
        "pending": dataset.get("pending", []),
        "evidence": dataset.get("evidence", {}),
    }
    seed_path.write_text(json.dumps(seed, ensure_ascii=False))
    toolsets_yaml = workdir / "toolsets.yaml"
    toolsets_yaml.write_text(
        "toolsets:\n"
        "  incident_bridge:\n"
        "    type: mcp\n"
        "    enabled: true\n"
        "    config:\n"
        "      mode: stdio\n"
        '      command: "python"\n'
        f'      args: ["{SHARED_REL}/mock_incident_bridge.py", "{seed_path}", "{out_path}"]\n'
        "  rca_evidence:\n"
        "    type: mcp\n"
        "    enabled: true\n"
        "    config:\n"
        "      mode: stdio\n"
        '      command: "python"\n'
        f'      args: ["{SHARED_REL}/mock_rca_evidence.py", "{seed_path}"]\n'
        + "".join(
            f"  {name}:\n    enabled: false\n"
            for name in (
                "kubernetes/core",
                "kubernetes/logs",
                "helm/core",
                "bash",
                "internet",
                "robusta",
                "connectivity_check",
            )
        )
    )
    return toolsets_yaml, out_path


def _read_incidents(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    try:
        return json.loads(out_path.read_text() or "[]")
    except json.JSONDecodeError:
        return []


def run_on(dataset: dict, model: str, wave_size: str, max_steps: int = 60) -> dict:
    # Imports deferred so the OFF path and unit tests need no holmes/LLM deps.
    from holmes.core.prompt import build_initial_ask_messages
    from holmes.core.tool_calling_llm import ToolCallingLLM
    from holmes.core.tools_utils.tool_executor import ToolExecutor
    from tests.llm.utils.test_case_utils import create_eval_llm
    from tests.llm.utils.test_toolset import TestToolsetManager

    alerts = dataset["alerts"]
    truth = {str(k): str(v) for k, v in dataset["ground_truth"].items()}

    with tempfile.TemporaryDirectory(prefix="grouping_run_") as tmp:
        workdir = Path(tmp)
        toolsets_yaml, out_path = _write_run_config(workdir, dataset)

        toolset_manager = TestToolsetManager(
            test_case_folder=str(workdir),
            toolsets_config_path=str(toolsets_yaml),
        )
        tool_executor = ToolExecutor(toolset_manager.toolsets)
        enabled = [t.name for t in tool_executor.enabled_toolsets]
        print(f"🛠️  enabled toolsets: {', '.join(enabled)}")
        if "incident_bridge" not in enabled or "rca_evidence" not in enabled:
            raise RuntimeError(f"mock toolsets failed to initialize; enabled={enabled}")

        ai = ToolCallingLLM(
            tool_executor=tool_executor,
            max_steps=max_steps,
            llm=create_eval_llm(model=model),
            tool_results_dir=workdir / "tool_results",
        )

        waves = _make_waves(alerts, wave_size)
        total_cost = 0.0
        t0 = time.monotonic()
        for i, wave in enumerate(waves, 1):
            leaders = _read_incidents(out_path)  # incidents opened by earlier waves
            prompt = render_triage_prompt(wave, leaders=leaders)
            messages = build_initial_ask_messages(
                initial_user_prompt=prompt,
                file_paths=None,
                tool_executor=tool_executor,
            )
            print(f"\n🌊 wave {i}/{len(waves)} — {len(wave)} alert(s), {len(leaders)} open incident(s)")
            result = ai.call(messages=messages)
            total_cost += float(result.total_cost or 0.0)
        elapsed = time.monotonic() - t0

        incidents = _read_incidents(out_path)

    predicted = incidents_to_partition(incidents)
    scores = score_all(predicted, truth)
    scores.update(cost_speed(total_cost, elapsed, len(alerts)))
    scores["n_incidents"] = len(incidents)
    return scores


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description="Multi-wave alert-grouping eval runner")
    ap.add_argument("--dataset", required=True, help="path to dataset.json")
    ap.add_argument("--mode", choices=["on", "off"], default="on")
    ap.add_argument("--model", default=os.environ.get("MODEL", "gpt-4.1"))
    ap.add_argument("--wave-size", default="all", help='"all" or an integer alerts-per-wave')
    args = ap.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text())
    if args.mode == "off":
        scores = run_off(dataset)
        label = "(grouping OFF baseline)"
    else:
        scores = run_on(dataset, model=args.model, wave_size=args.wave_size)
        label = f"(grouping ON, model={args.model}, wave_size={args.wave_size})"

    print("\n" + format_summary(scores, label))
    print(f"  incidents: {scores['n_incidents']} (from {len(dataset['alerts'])} alerts)")


if __name__ == "__main__":
    main(sys.argv[1:])
