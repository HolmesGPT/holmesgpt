"""Replay an eval run's LLM-judge prompts on candidate judges (see SKILL.md)."""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

import litellm

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "review-eval-run"))

from holmes.core.llm import DefaultLLM  # noqa: E402
from holmes.core.llm_usage import extract_usage_from_response  # noqa: E402
from review_eval_run import fetch_events, resolve_experiment_id  # noqa: E402
from tests.llm.utils.test_case_utils import create_eval_llm  # noqa: E402

BASELINE_JUDGE = "gpt-4.1-ci"
CHOICES = {1: "A", 0: "B"}


def extract_cases(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    spans = {e["span_id"]: e for e in events}
    cases = []
    for span in events:
        if (span.get("span_attributes") or {}).get("purpose") != "scorer":
            continue
        score_span = spans[span["span_parents"][0]]
        root = spans[span["root_span_id"]]
        metrics = span.get("metrics") or {}
        prompt_cost, completion_cost = litellm.cost_per_token(
            model="gpt-4.1",
            prompt_tokens=metrics.get("prompt_tokens") or 0,
            completion_tokens=metrics.get("completion_tokens") or 0,
        )
        cases.append({
            "id": span["span_id"],
            "test": root["span_attributes"]["name"],
            "messages": span["input"],
            "tools": span["metadata"]["tools"],
            "baseline": {
                "id": span["span_id"],
                "choice": CHOICES.get((score_span.get("scores") or {}).get("correctness")),
                "reasons": (score_span.get("metadata") or {}).get("rationale"),
                "latency": metrics.get("duration"),
                "cost": prompt_cost + completion_cost,
            },
        })
    return cases


def judge_case(judge: str, llm: DefaultLLM, case: Dict[str, Any]) -> Dict[str, Any]:
    start = time.time()
    try:
        response = llm.completion(messages=case["messages"], tools=case["tools"], tool_choice="auto")
    except Exception as e:
        return {"judge": judge, "id": case["id"], "error": str(e)}
    tool_calls = response.choices[0].message.tool_calls or []
    arguments = json.loads(tool_calls[0].function.arguments) if tool_calls else {}
    return {
        "judge": judge,
        "id": case["id"],
        "choice": arguments.get("choice"),
        "reasons": arguments.get("reasons") or response.choices[0].message.content,
        "latency": time.time() - start,
        "cost": extract_usage_from_response(response)["cost"],
    }


def read_jsonl(path: Path) -> Dict[str, Dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return {row["id"]: row for row in rows}


def load_cases(out_dir: Path, experiment: str) -> List[Dict[str, Any]]:
    cases_path = out_dir / "cases.json"
    if not cases_path.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        cases = extract_cases(fetch_events(resolve_experiment_id(experiment)))
        cases_path.write_text(json.dumps(cases))
        baseline_rows = [json.dumps(case["baseline"]) + "\n" for case in cases]
        (out_dir / f"{BASELINE_JUDGE}.jsonl").write_text("".join(baseline_rows))
    return json.loads(cases_path.read_text())


def run(out_dir: Path, cases: List[Dict[str, Any]], judges: List[str], workers: int) -> None:
    llms = {judge: create_eval_llm(judge) for judge in judges}
    pending = [(judge, case) for judge in judges for case in cases if case["id"] not in read_jsonl(out_dir / f"{judge}.jsonl")]
    print(f"Judging {len(pending)} prompts ({len(cases)} cases x {len(judges)} judges)")
    with ThreadPoolExecutor(workers) as pool:
        results = pool.map(judge_case, [j for j, _ in pending], [llms[j] for j, _ in pending], [c for _, c in pending])
        for result in results:
            if "error" in result:
                print(f"{result['judge']} {result['id']}: {result['error'][:200]}")
                continue
            with open(out_dir / f"{result['judge']}.jsonl", "a") as results_file:
                results_file.write(json.dumps(result) + "\n")


def report(out_dir: Path, cases: List[Dict[str, Any]], labels: Dict[str, str]) -> None:
    results = {path.stem: read_jsonl(path) for path in sorted(out_dir.glob("*.jsonl"))}
    truth: Dict[str, str] = {}
    disputed = []
    for case in cases:
        verdicts = {judge: rows.get(case["id"], {}) for judge, rows in results.items()}
        choices = {v.get("choice") for v in verdicts.values()} - {None}
        if case["id"] in labels or len(choices) == 1:
            truth[case["id"]] = labels.get(case["id"]) or choices.pop()
        else:
            disputed.append({"id": case["id"], "test": case["test"], "verdicts": verdicts, "prompt": case["messages"][-1]["content"]})
    (out_dir / "disputed.json").write_text(json.dumps(disputed, indent=2))
    print(f"Ground truth: {len(truth)} cases ({len(labels)} labeled); {len(disputed)} disputed -> {out_dir / 'disputed.json'}")
    print("\n## Judge Accuracy Comparison\n\n| Judge | Correct | False pass (FP) | False fail (FN) | No verdict | Total | Accuracy |")
    print("|---|---|---|---|---|---|---|")
    for judge, rows in results.items():
        choices = {case_id: rows.get(case_id, {}).get("choice") for case_id in truth}
        correct = sum(choices[c] == truth[c] for c in truth)
        false_pass = sum(choices[c] == "A" and truth[c] == "B" for c in truth)
        false_fail = sum(choices[c] == "B" and truth[c] == "A" for c in truth)
        no_verdict = len(truth) - correct - false_pass - false_fail
        print(f"| {judge} | {correct} | {false_pass} | {false_fail} | {no_verdict} | {len(truth)} | {100 * correct / max(len(truth), 1):.1f}% |")
    print("\n## Judge Cost Comparison\n\n| Judge | Judgments | Avg Cost | Min Cost | Max Cost | Total Cost |\n|---|---|---|---|---|---|")
    for judge, rows in results.items():
        costs = sorted(row.get("cost") or 0 for row in rows.values())
        print(f"| {judge} | {len(costs)} | ${sum(costs) / len(costs):.4f} | ${costs[0]:.4f} | ${costs[-1]:.4f} | ${sum(costs):.2f} |")
    print("\n## Judge Latency Comparison\n\n| Judge | Avg (s) | Min (s) | Max (s) | P50 (s) | P95 (s) |\n|---|---|---|---|---|---|")
    for judge, rows in results.items():
        latencies = sorted(row["latency"] for row in rows.values() if row.get("latency"))
        p50, p95 = (latencies[int(q * (len(latencies) - 1))] for q in (0.5, 0.95))
        print(f"| {judge} | {sum(latencies) / len(latencies):.1f} | {latencies[0]:.1f} | {latencies[-1]:.1f} | {p50:.1f} | {p95:.1f} |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", help="GitHub run id or Braintrust experiment name")
    parser.add_argument("--judges", default="", help="Comma-separated model_list names to replay")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--labels", type=Path, help="JSON {case_id: 'A'|'B'} from adjudicating disputed.json")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()

    out_dir = args.out_dir or Path(f"/tmp/benchmark-judge/{args.experiment}")
    cases = load_cases(out_dir, args.experiment)
    judges = [judge for judge in args.judges.split(",") if judge]
    if judges:
        run(out_dir, cases, judges, args.workers)
    report(out_dir, cases, json.loads(args.labels.read_text()) if args.labels else {})


if __name__ == "__main__":
    main()
