"""Print sanity checks for an eval benchmark run (see SKILL.md).

Pass/fail, answers, provider errors and skill-check failures come from the
run's pytest report (eval_results.json); Braintrust only adds trace links and
tool spans, because it drops rows.
"""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from holmes.core.tracing import BRAINTRUST_ORG, BRAINTRUST_PROJECT  # noqa: E402
from tests.llm.utils.braintrust_history import (  # noqa: E402
    _get_api_key,
    _get_project_id,
    _make_api_request,
)
from tests.llm.utils.reporting.eval_run_review import (  # noqa: E402
    EvalRun,
    ModelComparison,
    cache_usage,
    compare_with_baseline,
    group_runs,
    iteration_count,
    missing_from_braintrust,
    parse_markdown_report,
    parse_results,
    suspect_tests,
)

DEFAULT_REPO = "HolmesGPT/holmesgpt"
RESULTS_FILE = "eval_results.json"


# --- Inputs -----------------------------------------------------------------


def gh_api(path: str) -> bytes:
    result = subprocess.run(["gh", "api", path], capture_output=True)
    if result.returncode != 0:
        sys.exit(f"gh api {path} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def download_results(run_id: str, repo: str) -> Path:
    """Fetch eval_results.json from the run's eval-results-<run_id> artifact (cached)."""
    target = Path(tempfile.gettempdir()) / "review-eval-run" / run_id / RESULTS_FILE
    if target.exists():
        return target
    artifacts = json.loads(gh_api(f"repos/{repo}/actions/runs/{run_id}/artifacts"))["artifacts"]
    artifact = next((a for a in artifacts if a["name"] == f"eval-results-{run_id}"), None)
    if not artifact or artifact.get("expired"):
        names = ", ".join(a["name"] for a in artifacts) or "none"
        sys.exit(f"Run {run_id} has no unexpired eval-results-{run_id} artifact (artifacts: {names})")
    with zipfile.ZipFile(io.BytesIO(gh_api(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"))) as archive:
        member = next((n for n in archive.namelist() if Path(n).name == RESULTS_FILE), None)
        if not member:
            sys.exit(f"Artifact {artifact['id']} contains no {RESULTS_FILE}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(member))
    return target


def load_runs(source: str, repo: str) -> Tuple[List[EvalRun], Optional[str], Dict[str, float]]:
    """Runs from eval_results.json, a GitHub run id or a committed markdown report.

    Also returns the run id (when given one) and per-model total costs that
    override the per-run sum (markdown reports round per-run cost to cents).
    """
    path = Path(source)
    run_id = source if source.isdigit() and not path.exists() else None
    if run_id:
        path = download_results(run_id, repo)
    if path.suffix == ".md":
        runs, total_costs = parse_markdown_report(path.read_text())
        if not runs:
            sys.exit(f"{path} has no Detailed Raw Results table")
        return runs, run_id, total_costs
    with open(path) as f:
        return parse_results(json.load(f)), run_id, {}


def fetch_braintrust_events(experiment_name: str) -> List[Dict[str, Any]]:
    result = _make_api_request(
        "/experiment",
        params={"experiment_name": experiment_name, "project_id": _get_project_id()},
    )
    experiments = (result or {}).get("objects", [])
    if not experiments:
        print(f"WARNING: Braintrust experiment {experiment_name} not found; no traces or tool spans.")
        return []
    events: List[Dict[str, Any]] = []
    cursor = None
    for _ in range(100):
        body: Dict[str, Any] = {"limit": 1000, **({"cursor": cursor} if cursor else {})}
        page = _make_api_request(
            f"/experiment/{experiments[0]['id']}/fetch", method="POST", json_data=body
        )
        if not page or not page.get("events"):
            break
        events += page["events"]
        cursor = page.get("cursor")
        if not cursor:
            break
    return events


def load_braintrust(source: Optional[str], runs: List[EvalRun], run_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Events from a cached JSON list, an experiment name/run id, or the report's experiment."""
    if source and Path(source).is_file():
        with open(source) as f:
            return json.load(f)
    name = source or next((r.experiment for r in runs if r.experiment), None) or run_id
    if name and name.isdigit():
        name = f"ci-benchmark-{name}"
    if not name:
        print("WARNING: no Braintrust experiment known for this report; pass --braintrust.")
        return None
    if not _get_api_key():
        print(f"WARNING: BRAINTRUST_API_KEY is not set; skipping Braintrust ({name}).")
        return None
    return fetch_braintrust_events(name)


def trace_url(run: EvalRun) -> str:
    experiment = quote(run.experiment or "", safe="")
    return (
        f"https://www.braintrust.dev/app/{BRAINTRUST_ORG}/p/{BRAINTRUST_PROJECT}/"
        f"experiments/{experiment}?c=&r={run.root_span_id}&s={run.root_span_id}"
    )


# --- Output -----------------------------------------------------------------


def pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.0f}%"


def print_braintrust_coverage(runs: List[EvalRun], bt_roots: Set[str]) -> None:
    missing = missing_from_braintrust(runs, bt_roots)
    if not missing:
        print(f"Braintrust has all {len(runs)} runs.")
        return
    print(
        f"\n**WARNING: Braintrust is missing {len(missing)} of {len(runs)} runs** "
        f"(pass/fail below still comes from pytest; these have no trace link or tool spans):"
    )
    for run in missing:
        print(f"- {run.test} × {run.model}")


def print_pass_rates(comparisons: List[ModelComparison], exclude: Set[str], has_baseline: bool) -> None:
    note = f"; clean also drops {', '.join(sorted(exclude))}" if exclude else ""
    print(f"\n## Pass rate and cost\n\nClean = executed runs only{note}. Cost = sum of per-test cost.\n")
    if not has_baseline:
        print("| Model | Raw | Clean | Not executed | Cost |\n|---|---|---|---|---|")
        for c in comparisons:
            cur = c.current
            print(
                f"| {c.model} | {cur.passed}/{cur.runs} {pct(cur.raw_rate)} | "
                f"{cur.clean_passed}/{cur.clean_runs} {pct(cur.clean_rate)} | {cur.not_executed} | ${cur.cost:.2f} |"
            )
        return
    print("| Model | Raw | Clean | Baseline raw | Baseline clean | Cost | Baseline cost | Flag |")
    print("|---|---|---|---|---|---|---|---|")
    for c in comparisons:
        cur, base = c.current, c.baseline
        row = (
            f"| {c.model} | {cur.passed}/{cur.runs} {pct(cur.raw_rate)} | "
            f"{cur.clean_passed}/{cur.clean_runs} {pct(cur.clean_rate)} | "
        )
        if base is None:
            print(row + f"no baseline | | ${cur.cost:.2f} | | |")
            continue
        flag = f"COST SPIKE ×{c.cost_ratio:.1f}" if c.cost_spike else ""
        print(
            row + f"{base.passed}/{base.runs} {pct(base.raw_rate)} | "
            f"{base.clean_passed}/{base.clean_runs} {pct(base.clean_rate)} | "
            f"${cur.cost:.2f} | ${base.cost:.2f} | {flag} |"
        )


def print_regressions(comparisons: List[ModelComparison], baseline_models: Set[str]) -> None:
    total = sum(len(c.regressions) for c in comparisons)
    print(f"\n## Regressions vs baseline (passed every baseline run, failed now): {total}\n")
    for c in comparisons:
        if c.regressions:
            tests = ", ".join(
                f"{r.test} ({r.now}{', excluded' if r.excluded else ''}{', skill check' if r.skill_check else ''})"
                for r in c.regressions
            )
            print(f"- {c.model}: {tests}")
    current_models = {c.model for c in comparisons}
    if baseline_models - current_models:
        print(f"- Only in baseline (dropped): {', '.join(sorted(baseline_models - current_models))}")


def print_not_executed(runs: List[EvalRun]) -> None:
    skipped = [r for r in runs if not r.executed]
    print(f"\n## Never-executed runs (left out of clean rates): {len(skipped)}\n")
    for run in skipped:
        print(f"- {run.not_executed.value if run.not_executed else ''} {run.test} × {run.model}: {run.error[:160]}")


def print_skill_check_failures(runs: List[EvalRun]) -> None:
    failed = [r for r in runs if r.skill_check]
    print(f"\n## Skill-check failures: {len(failed)}\n")
    if failed:
        print("| Test | Model | Reason | Judge | Suggested skill(s) |\n|---|---|---|---|---|")
    for run in failed:
        judge = {True: "correct", False: "wrong", None: "not recorded"}[run.judge_correct]
        reason = run.skill_check.label if run.skill_check else ""
        print(f"| {run.test} | {run.model} | {reason} | {judge} | {'; '.join(run.suggested_skills) or '-'} |")


def print_suspect_tests(runs: List[EvalRun]) -> None:
    iterations = iteration_count(runs)
    print(f"\n## Suspect tests ({iterations} iteration{'s' if iterations != 1 else ''} per test)\n")
    print("| Test | Pattern | Per-model passes (executed runs) |\n|---|---|---|")
    for suspect in suspect_tests(runs):
        counts = ", ".join(f"{m} {p}/{n}" for m, (p, n) in suspect.per_model.items())
        print(f"| {suspect.test} | {suspect.pattern} | {counts} |")


def print_cache_table(runs: List[EvalRun], min_cache: float) -> None:
    print("\n## Prompt caching\n\n| Model | Runs reporting | Prompt tokens | Cached | Cache % | Flag |")
    print("|---|---|---|---|---|---|")
    for model, usage in sorted(cache_usage(runs).items()):
        flag = "NOT REPORTED" if usage.cached_tokens == 0 else ("LOW" if usage.ratio < min_cache else "")
        print(
            f"| {model} | {usage.reported_runs} | {usage.prompt_tokens} | {usage.cached_tokens} | "
            f"{usage.ratio:.0f}% | {flag} |"
        )


def print_tool_errors(runs: List[EvalRun], tools: List[Dict[str, Any]]) -> None:
    root_to_test = {r.root_span_id: r.test for r in runs if r.root_span_id}
    calls: Dict[str, int] = defaultdict(int)
    errors: Dict[str, Counter] = defaultdict(Counter)
    for tool in tools:
        test = root_to_test.get(tool["root_span_id"])
        error = (tool.get("metadata") or {}).get("error")
        if test:
            calls[test] += 1
            if error:
                errors[test][f"{tool['span_attributes'].get('name')}: {str(error)[:80]}"] += 1
    noisy = [(t, sum(errors[t].values()), calls[t]) for t in calls if sum(errors[t].values()) * 4 >= calls[t]]
    print("\n## Tests with >=25% tool errors (Braintrust tool spans)\n")
    for test, error_count, call_count in sorted(noisy, key=lambda x: -x[1] / x[2]):
        top, top_count = errors[test].most_common(1)[0]
        print(f"- {test}: {error_count}/{call_count}, most often ({top_count}×) {top}")


def print_model_failures(runs: List[EvalRun], tools: List[Dict[str, Any]], bt_roots: Set[str], model: str) -> None:
    tools_by_root: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for tool in sorted(tools, key=lambda t: t["created"]):
        tools_by_root[tool["root_span_id"]].append(tool)
    failures = [r for r in runs if r.model == model and not r.passed]
    print(f"\n## Failures for {model}: {len(failures)}")
    for run in failures:
        status = run.not_executed.value if run.not_executed else ("skill check" if run.skill_check else "failed")
        print(f"\n### {run.test} ({status})")
        if run.root_span_id in bt_roots:
            print(f"TRACE: {trace_url(run)}")
        if run.error:
            print(f"ERROR: {run.error}")
        print(f"PROMPT: {run.user_prompt}\nEXPECTED: {run.expected}")
        print(f"OUTPUT: {run.output[:1500]}")
        span_tools = tools_by_root.get(run.root_span_id or "", [])
        for tool in span_tools:
            print(f"- TOOL {tool['span_attributes'].get('name')} {json.dumps(tool.get('input'))[:200]}")
            print(f"  -> {str(tool.get('output'))[:200]!r}")
        if not span_tools and run.tools_called:
            print(f"TOOLS CALLED: {', '.join(run.tools_called)[:600]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", help="Path to eval_results.json, or a GitHub run id (downloads its artifact)")
    parser.add_argument(
        "--baseline",
        help="Baseline eval_results.json, GitHub run id, or committed markdown report",
    )
    parser.add_argument(
        "--exclude-tests",
        default="",
        help="Comma-separated eval-bug tests left out of the clean pass rates",
    )
    parser.add_argument(
        "--braintrust",
        help="Braintrust experiment name, run id, or a cached JSON list of events "
        "(default: the experiment named in the report)",
    )
    parser.add_argument("--no-braintrust", action="store_true", help="Skip Braintrust entirely")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--min-cache", type=float, default=50)
    parser.add_argument("--model", help="Dump every failure for this model")
    args = parser.parse_args()

    exclude = {t.strip() for t in args.exclude_tests.split(",") if t.strip()}
    runs, run_id, _ = load_runs(args.results, args.repo)
    baseline_runs, _, baseline_costs = load_runs(args.baseline, args.repo) if args.baseline else (None, None, {})
    grouped = group_runs(runs)
    models = {r.model for r in runs}
    print(
        f"# {args.results}: {len(runs)} runs ({len(models)} models × {len(grouped)} tests, "
        f"{iteration_count(runs)} iteration(s))"
    )

    events = None if args.no_braintrust else load_braintrust(args.braintrust, runs, run_id)
    evals = [e for e in events or [] if (e.get("span_attributes") or {}).get("type") == "eval"]
    tools = [e for e in events or [] if (e.get("span_attributes") or {}).get("type") == "tool"]
    bt_roots = {e["root_span_id"] for e in evals}
    if events is not None:
        print_braintrust_coverage(runs, bt_roots)

    comparisons = compare_with_baseline(runs, baseline_runs, exclude, baseline_costs)
    print_pass_rates(comparisons, exclude, baseline_runs is not None)
    if baseline_runs is not None:
        print_regressions(comparisons, {r.model for r in baseline_runs})
    print_not_executed(runs)
    print_skill_check_failures(runs)
    print_suspect_tests(runs)
    print_cache_table(runs, args.min_cache)
    if events is not None:
        print_tool_errors(runs, tools)
    if args.model:
        print_model_failures(runs, tools, bt_roots, args.model)


if __name__ == "__main__":
    main()
