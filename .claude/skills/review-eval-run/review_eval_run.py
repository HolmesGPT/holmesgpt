"""Print sanity checks for a Braintrust eval experiment (see SKILL.md)."""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.llm.utils.braintrust_history import (  # noqa: E402
    _get_project_id,
    _make_api_request,
)


def resolve_experiment_id(name: str) -> str:
    experiment_name = f"ci-benchmark-{name}" if name.isdigit() else name
    result = _make_api_request(
        "/experiment",
        params={"experiment_name": experiment_name, "project_id": _get_project_id()},
    )
    experiments = (result or {}).get("objects", [])
    if not experiments:
        sys.exit(f"Experiment {experiment_name} not found (is BRAINTRUST_API_KEY set?)")
    return experiments[0]["id"]


def fetch_events(experiment_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    cursor = None
    for _ in range(100):
        body: Dict[str, Any] = {"limit": 1000, **({"cursor": cursor} if cursor else {})}
        page = _make_api_request(
            f"/experiment/{experiment_id}/fetch", method="POST", json_data=body
        )
        if not page or not page.get("events"):
            break
        events += page["events"]
        cursor = page.get("cursor")
        if not cursor:
            break
    return events


def test_name(span: Dict[str, Any]) -> str:
    return span["span_attributes"]["name"].split("[")[0]


def passed(span: Dict[str, Any]) -> bool:
    return (span.get("scores") or {}).get("correctness") == 1


def print_cache_table(evals: List[Dict[str, Any]], min_cache: float) -> None:
    prompt_tokens: Dict[str, int] = defaultdict(int)
    cached_tokens: Dict[str, int] = defaultdict(int)
    for span in evals:
        metadata = span.get("metadata") or {}
        prompt_tokens[metadata.get("model")] += metadata.get("prompt_tokens") or 0
        cached_tokens[metadata.get("model")] += metadata.get("cached_tokens") or 0
    print("\n## Prompt caching\n\n| Model | Prompt tokens | Cached | Cache % | Flag |")
    print("|---|---|---|---|---|")
    for model in sorted(prompt_tokens):
        ratio = 100 * cached_tokens[model] / max(prompt_tokens[model], 1)
        flag = "NOT REPORTED" if cached_tokens[model] == 0 else ("LOW" if ratio < min_cache else "")
        print(f"| {model} | {prompt_tokens[model]} | {cached_tokens[model]} | {ratio:.0f}% | {flag} |")


def print_suspect_tests(evals: List[Dict[str, Any]]) -> None:
    runs: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for span in sorted(evals, key=lambda s: s["created"]):
        runs[test_name(span)][(span.get("metadata") or {}).get("model")].append(span)
    print("\n## Suspect tests\n\n| Test | Pattern | Per-model passes |\n|---|---|---|")
    for test, by_model in sorted(runs.items()):
        results = {model: [passed(s) for s in spans] for model, spans in by_model.items()}
        first_only = sum(r[0] and not any(r[1:]) for r in results.values() if len(r) > 1)
        if not any(any(r) for r in results.values()):
            pattern = "fails for every model"
        elif first_only * 2 >= len(results):
            pattern = "passes only on first iteration (state decay?)"
        elif not any(all(r) for r in results.values()):
            pattern = "no model passes every iteration"
        else:
            continue
        counts = ", ".join(f"{m} {sum(r)}/{len(r)}" for m, r in sorted(results.items()))
        print(f"| {test} | {pattern} | {counts} |")


def print_errors(evals: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> None:
    unscored = [s["span_attributes"]["name"] for s in evals if not s.get("scores")]
    print(f"\n## Unscored (errored/skipped) runs: {len(unscored)}")
    for name in unscored:
        print(f"- {name}")
    root_to_test = {s["root_span_id"]: test_name(s) for s in evals}
    calls: Dict[str, int] = defaultdict(int)
    errors: Dict[str, int] = defaultdict(int)
    for tool in tools:
        test = root_to_test.get(tool["root_span_id"])
        calls[test] += 1
        errors[test] += bool((tool.get("metadata") or {}).get("error"))
    noisy = [(t, errors[t], calls[t]) for t in calls if t and errors[t] * 4 >= calls[t] > 0]
    print("\n## Tests with >=25% tool errors\n")
    for test, error_count, call_count in sorted(noisy, key=lambda x: -x[1] / x[2]):
        print(f"- {test}: {error_count}/{call_count}")


def print_model_failures(evals: List[Dict[str, Any]], tools: List[Dict[str, Any]], model: str) -> None:
    tools_by_root: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for tool in sorted(tools, key=lambda t: t["created"]):
        tools_by_root[tool["root_span_id"]].append(tool)
    failures = [s for s in evals if (s.get("metadata") or {}).get("model") == model and not passed(s)]
    print(f"\n## Failures for {model}: {len(failures)}")
    for span in failures:
        print(f"\n### {span['span_attributes']['name']} ({span['created']})")
        print(f"PROMPT: {span.get('input')}\nEXPECTED: {json.dumps(span.get('expected'))}")
        print(f"OUTPUT: {str(span.get('output'))[:1500]}")
        for tool in tools_by_root[span["root_span_id"]]:
            print(f"- TOOL {tool['span_attributes'].get('name')} {json.dumps(tool.get('input'))[:200]}")
            print(f"  -> {str(tool.get('output'))[:200]!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", help="GitHub run id or Braintrust experiment name")
    parser.add_argument("--min-cache", type=float, default=50)
    parser.add_argument("--model", help="Dump every failure for this model")
    args = parser.parse_args()

    events = fetch_events(resolve_experiment_id(args.experiment))
    evals = [e for e in events if (e.get("span_attributes") or {}).get("type") == "eval"]
    tools = [e for e in events if (e.get("span_attributes") or {}).get("type") == "tool"]
    print(f"# {args.experiment}: {len(evals)} eval runs")
    print_cache_table(evals, args.min_cache)
    print_suspect_tests(evals)
    print_errors(evals, tools)
    if args.model:
        print_model_failures(evals, tools, args.model)


if __name__ == "__main__":
    main()
