"""Analysis behind the review-eval-run skill (.claude/skills/review-eval-run).

The pytest JSON report (`eval_results.json`) is the source of truth for every
run: Braintrust drops rows, so it only adds trace links and tool spans. A
committed markdown report can stand in as a baseline when the run's artifact
has expired; it carries only status and cost per run.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from tests.generate_eval_report import (
    get_test_infra_error,
    get_test_skill_check_failure,
    merge_user_properties,
)
from tests.llm.utils.reporting.infra_errors import InfraError
from tests.llm.utils.reporting.skill_check import (
    SkillCheckFailure,
    judge_verdict,
    suggested_skill_titles,
)

# Default sentinel for `actual` (property_manager.py), overwritten once the
# model answers.
NOT_EXECUTED_OUTPUT = "Test not executed"
COST_SPIKE_RATIO = 1.5


class NotExecuted(str, Enum):
    """Why a run never produced a model answer; it says nothing about the model."""

    PROVIDER = "⚠️ provider error"
    TIMEOUT = "⏱️ timeout/rate limit"
    SETUP = "setup failure"
    MOCK = "mock data failure"
    SKIPPED = "skipped"
    CRASH = "crashed before answering"


@dataclass
class EvalRun:
    test: str
    model: str
    nodeid: str
    passed: bool
    not_executed: Optional[NotExecuted] = None
    skill_check: Optional[SkillCheckFailure] = None
    judge_correct: Optional[bool] = None
    suggested_skills: List[str] = field(default_factory=list)
    cost: float = 0.0
    prompt_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    user_prompt: str = ""
    expected: str = ""
    output: str = ""
    tools_called: List[str] = field(default_factory=list)
    error: str = ""
    root_span_id: Optional[str] = None
    experiment: Optional[str] = None

    @property
    def executed(self) -> bool:
        return self.not_executed is None

    @property
    def failed(self) -> bool:
        return self.executed and not self.passed


def _natural_key(text: str) -> List[Any]:
    # Iterations of one test share an id; pytest suffixes them 0, 1, ..., 10.
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text)]


def _not_executed(test: Dict[str, Any], props: Dict[str, Any]) -> Optional[NotExecuted]:
    outcome = test.get("outcome")
    if outcome == "passed":
        return None
    if outcome == "skipped":
        return NotExecuted.SKIPPED
    if props.get("is_setup_failure") or outcome == "error":
        return NotExecuted.SETUP
    if props.get("mock_data_failure"):
        return NotExecuted.MOCK
    infra = get_test_infra_error(test)
    if infra is InfraError.TIMEOUT:
        return NotExecuted.TIMEOUT
    if infra is InfraError.PROVIDER:
        return NotExecuted.PROVIDER
    if props.get("actual") == NOT_EXECUTED_OUTPUT:
        return NotExecuted.CRASH
    return None


def _ids_from_nodeid(nodeid: str) -> Tuple[str, str]:
    params = nodeid[nodeid.find("[") + 1 : nodeid.rfind("]")]
    test, _, model = params.partition("-")
    return test, model


def _optional_int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) else None


def parse_results(report: Dict[str, Any]) -> List[EvalRun]:
    """One EvalRun per executed pytest item, iterations in collection order."""
    runs = []
    for test in report.get("tests", []):
        if test.get("outcome") == "deselected":
            continue
        props = merge_user_properties(test)
        nodeid = test.get("nodeid", "")
        fallback_test, fallback_model = _ids_from_nodeid(nodeid)
        skill_check = get_test_skill_check_failure(test)
        error = ""
        if props.get("error_type") or props.get("error_message"):
            message = str(props.get("error_message") or "").strip().splitlines()
            error = f"{props.get('error_type') or '?'}: {message[0] if message else ''}"
        runs.append(
            EvalRun(
                test=props.get("clean_test_case_id") or fallback_test,
                model=props.get("model") or fallback_model,
                nodeid=nodeid,
                passed=test.get("outcome") == "passed",
                not_executed=_not_executed(test, props),
                skill_check=skill_check,
                judge_correct=judge_verdict(props) if skill_check else None,
                suggested_skills=suggested_skill_titles(props),
                cost=float(props.get("cost") or 0),
                prompt_tokens=_optional_int(props.get("prompt_tokens")),
                cached_tokens=_optional_int(props.get("cached_tokens")),
                user_prompt=str(props.get("user_prompt") or ""),
                expected=str(props.get("expected") or ""),
                output=str(props.get("actual") or ""),
                tools_called=[str(t) for t in props.get("tools_called") or []],
                error=error,
                root_span_id=props.get("braintrust_root_span_id"),
                experiment=props.get("braintrust_experiment"),
            )
        )
    return sorted(runs, key=lambda r: _natural_key(r.nodeid))


def group_runs(runs: Iterable[EvalRun]) -> Dict[str, Dict[str, List[EvalRun]]]:
    grouped: Dict[str, Dict[str, List[EvalRun]]] = defaultdict(lambda: defaultdict(list))
    for run in runs:
        grouped[run.test][run.model].append(run)
    return grouped


def iteration_count(runs: Iterable[EvalRun]) -> int:
    grouped = group_runs(runs)
    return max((len(r) for by_model in grouped.values() for r in by_model.values()), default=0)


@dataclass
class SuspectTest:
    test: str
    pattern: str
    # model -> (passes, executed runs)
    per_model: Dict[str, Tuple[int, int]]


def suspect_tests(runs: List[EvalRun]) -> List[SuspectTest]:
    """Tests whose failure pattern points at the eval rather than the models.

    Only executed runs count. With one iteration the only signal is breadth:
    a test failing for at least half the models that ran it (two or more).
    """
    single_iteration = iteration_count(runs) <= 1
    suspects = []
    for test, by_model in sorted(group_runs(runs).items()):
        results = {
            model: [r.passed for r in model_runs if r.executed]
            for model, model_runs in by_model.items()
        }
        results = {m: r for m, r in results.items() if r}
        if not results:
            continue
        if single_iteration:
            failing = [m for m, r in results.items() if not r[0]]
            if len(results) < 2 or len(failing) * 2 < len(results):
                continue
            pattern = f"fails for {len(failing)}/{len(results)} models that ran it"
            results = {m: results[m] for m in failing}
        else:
            first_only = sum(r[0] and not any(r[1:]) for r in results.values() if len(r) > 1)
            if not any(any(r) for r in results.values()):
                pattern = "fails for every model"
            elif first_only * 2 >= len(results):
                pattern = "passes only on first iteration (state decay?)"
            elif not any(all(r) for r in results.values()):
                pattern = "no model passes every iteration"
            else:
                continue
        suspects.append(
            SuspectTest(test, pattern, {m: (sum(r), len(r)) for m, r in sorted(results.items())})
        )
    return suspects


@dataclass
class ModelSummary:
    runs: int = 0
    passed: int = 0
    # Executed runs of tests not excluded as eval bugs.
    clean_runs: int = 0
    clean_passed: int = 0
    not_executed: int = 0
    cost: float = 0.0

    @property
    def raw_rate(self) -> Optional[float]:
        return 100 * self.passed / self.runs if self.runs else None

    @property
    def clean_rate(self) -> Optional[float]:
        return 100 * self.clean_passed / self.clean_runs if self.clean_runs else None


def summarize(
    runs: Iterable[EvalRun],
    exclude_tests: Set[str],
    total_costs: Optional[Dict[str, float]] = None,
) -> Dict[str, ModelSummary]:
    summaries: Dict[str, ModelSummary] = defaultdict(ModelSummary)
    for run in runs:
        summary = summaries[run.model]
        summary.runs += 1
        summary.passed += run.passed
        summary.not_executed += not run.executed
        summary.cost += run.cost
        if run.executed and run.test not in exclude_tests:
            summary.clean_runs += 1
            summary.clean_passed += run.passed
    for model, cost in (total_costs or {}).items():
        if model in summaries:
            summaries[model].cost = cost
    return dict(summaries)


@dataclass
class Regression:
    test: str
    # "passes/executed runs" now, e.g. "0/1".
    now: str
    excluded: bool
    # Failed only the skill-suggestion check, not necessarily the answer.
    skill_check: bool = False


@dataclass
class ModelComparison:
    model: str
    current: ModelSummary
    baseline: Optional[ModelSummary]
    regressions: List[Regression]

    @property
    def cost_ratio(self) -> Optional[float]:
        if not self.baseline or self.baseline.cost <= 0:
            return None
        return self.current.cost / self.baseline.cost

    @property
    def cost_spike(self) -> bool:
        ratio = self.cost_ratio
        return ratio is not None and ratio > COST_SPIKE_RATIO


def find_regressions(
    current: List[EvalRun], baseline: List[EvalRun], exclude_tests: Set[str]
) -> Dict[str, List[Regression]]:
    """Per model: tests every executed baseline iteration passed and that now fail at least once."""
    current_grouped = group_runs(current)
    baseline_grouped = group_runs(baseline)
    regressions: Dict[str, List[Regression]] = defaultdict(list)
    for test, by_model in sorted(current_grouped.items()):
        for model, model_runs in sorted(by_model.items()):
            executed_now = [r for r in model_runs if r.executed]
            executed_before = [
                r for r in baseline_grouped.get(test, {}).get(model, []) if r.executed
            ]
            if not executed_before or not all(r.passed for r in executed_before):
                continue
            if not any(r.failed for r in executed_now):
                continue
            passes = sum(r.passed for r in executed_now)
            regressions[model].append(
                Regression(
                    test,
                    f"{passes}/{len(executed_now)}",
                    test in exclude_tests,
                    any(r.skill_check for r in executed_now if r.failed),
                )
            )
    return dict(regressions)


def compare_with_baseline(
    current: List[EvalRun],
    baseline: Optional[List[EvalRun]],
    exclude_tests: Set[str],
    baseline_costs: Optional[Dict[str, float]] = None,
) -> List[ModelComparison]:
    current_summary = summarize(current, exclude_tests)
    baseline_summary = summarize(baseline or [], exclude_tests, baseline_costs)
    regressions = find_regressions(current, baseline or [], exclude_tests)
    return [
        ModelComparison(
            model,
            current_summary[model],
            baseline_summary.get(model),
            regressions.get(model, []),
        )
        for model in sorted(current_summary)
    ]


@dataclass
class CacheUsage:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    # Runs that reported token usage at all.
    reported_runs: int = 0

    @property
    def ratio(self) -> float:
        return 100 * self.cached_tokens / max(self.prompt_tokens, 1)


def cache_usage(runs: Iterable[EvalRun]) -> Dict[str, CacheUsage]:
    usage: Dict[str, CacheUsage] = defaultdict(CacheUsage)
    for run in runs:
        if run.prompt_tokens is None:
            continue
        model_usage = usage[run.model]
        model_usage.prompt_tokens += run.prompt_tokens
        model_usage.cached_tokens += run.cached_tokens or 0
        model_usage.reported_runs += 1
    return dict(usage)


def missing_from_braintrust(runs: Iterable[EvalRun], braintrust_root_span_ids: Set[str]) -> List[EvalRun]:
    return [r for r in runs if r.root_span_id not in braintrust_root_span_ids]


# --- Committed markdown reports (tests/generate_eval_report.py output) -------

_LINK = re.compile(r"\]\([^)]*\)")
_CELL = re.compile(r"\[(\S+) \d+% \((\d+)/(\d+)\)\]([^|]*)")
_CELL_COST = re.compile(r"💰 \$([\d.]+)")
_TOTAL_COST = re.compile(r"\$([\d.]+)")
_STATUS_NOT_EXECUTED = {
    "⚠️": NotExecuted.PROVIDER,  # setup or provider failure
    "⏱️": NotExecuted.TIMEOUT,
    "⏭️": NotExecuted.SKIPPED,
    "🔧": NotExecuted.MOCK,
}


def _table(lines: List[str], heading: str) -> List[List[str]]:
    """Cell rows of the first table under `heading`, header row first, links stripped."""
    rows: List[List[str]] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            if rows:
                break
            in_section = line.strip() == heading
        elif in_section and line.startswith("|"):
            cells = [c.strip() for c in _LINK.sub("]", line).strip().strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):
                rows.append(cells)
        elif rows:
            break
    return rows


def _report_test_name(cell: str) -> str:
    name = cell.replace("[🔗]", "").replace("**", "").strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name


def parse_markdown_report(text: str) -> Tuple[List[EvalRun], Dict[str, float]]:
    """Runs from a report's Detailed Raw Results table, plus each model's total cost.

    Reports from before ROB-1714 show a provider error as a plain failure; a
    failed run with no cost never got a model response, so it counts as not
    executed.
    """
    lines = text.splitlines()
    runs = []
    detailed = _table(lines, "## Detailed Raw Results")
    if detailed:
        models = detailed[0][1:]
        for row in detailed[1:]:
            test = _report_test_name(row[0])
            for model, cell in zip(models, row[1:]):
                for status, passes, total, rest in _CELL.findall(cell):
                    passes_count, total_count = int(passes), int(total)
                    cost_match = _CELL_COST.search(rest)
                    not_executed = _STATUS_NOT_EXECUTED.get(status)
                    if not not_executed and not cost_match and passes_count < total_count:
                        not_executed = NotExecuted.CRASH
                    for i in range(total_count):
                        passed = i < passes_count
                        runs.append(
                            EvalRun(
                                test=test,
                                model=model,
                                nodeid=f"{test}-{model}-{i}",
                                passed=passed,
                                not_executed=None if passed else not_executed,
                                cost=float(cost_match.group(1)) / total_count if cost_match else 0.0,
                            )
                        )
    total_costs: Dict[str, float] = {}
    cost_table = _table(lines, "## Model Cost Comparison")
    if cost_table and "Total Cost" in cost_table[0]:
        column = cost_table[0].index("Total Cost")
        for row in cost_table[1:]:
            match = _TOTAL_COST.search(row[column]) if len(row) > column else None
            if match:
                total_costs[row[0]] = float(match.group(1))
    return runs, total_costs
