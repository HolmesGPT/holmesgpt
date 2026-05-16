"""Fetch the latest weekly benchmark results from Braintrust for comparison.

Compares current eval results against the most recent ci-benchmark experiment
(the weekly scheduled benchmark run on master). This requires only 2-3 API calls
total: one to find the benchmark experiment, and 1-2 to paginate its eval spans.
"""

import logging
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests  # type: ignore[import-untyped]

from holmes.core.tracing import BRAINTRUST_ORG, BRAINTRUST_PROJECT

# Braintrust API base URL
BRAINTRUST_API_URL = "https://api.braintrust.dev/v1"

# CI benchmark experiment name prefix (set by eval-benchmarks.yaml workflow)
BENCHMARK_EXPERIMENT_PREFIX = "ci-benchmark-"
# Post-merge eval that runs the regression set on every master push.
# Workflow: .github/workflows/eval-master.yaml. Experiment name pattern: master-<run_id>.
MASTER_EXPERIMENT_PREFIX = "master-"
MASTER_WORKFLOW = "eval-master.yaml"

__all__ = [
    "BRAINTRUST_ORG",
    "BRAINTRUST_PROJECT",
    "BenchmarkMetrics",
    "HistoricalComparison",
    "HistoricalComparisonDetails",
    "ExperimentInfo",
    "get_benchmark_baseline",
    "get_master_baseline",
    "compare_with_benchmark",
]


def _get_api_key() -> Optional[str]:
    """Get the Braintrust API key from environment.

    Checks BRAINTRUST_API_KEY first, then falls back to BRAINTRUST_SERVICE_TOKEN.
    """
    return os.environ.get("BRAINTRUST_API_KEY") or os.environ.get(
        "BRAINTRUST_SERVICE_TOKEN"
    )


@dataclass
class BenchmarkMetrics:
    """Metrics for a single test case from the benchmark run."""

    test_id: str
    model: str
    passed: bool = False
    duration: Optional[float] = None
    cost: Optional[float] = None
    tool_call_count: Optional[int] = None
    num_llm_calls: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None



@dataclass
class HistoricalComparison:
    """Comparison data between current and benchmark metrics."""

    test_id: str
    model: str
    current_duration: Optional[float] = None
    historical_avg_duration: Optional[float] = None
    duration_diff_pct: Optional[float] = None  # Positive = slower, negative = faster
    current_cost: Optional[float] = None
    historical_avg_cost: Optional[float] = None
    cost_diff_pct: Optional[float] = None
    current_passed: Optional[bool] = None
    benchmark_passed: Optional[bool] = None
    sample_count: int = 1


@dataclass
class ExperimentInfo:
    """Information about a benchmark experiment."""

    id: str
    name: str
    branch: str
    created: Optional[str] = None


@dataclass
class HistoricalComparisonDetails:
    """Details about the benchmark comparison for transparency."""

    experiments: List[ExperimentInfo] = field(default_factory=list)
    filter_description: str = ""
    status: str = ""  # Empty = success, otherwise explains why data is missing
    errors: List[str] = field(default_factory=list)
    project_id: Optional[str] = None
    metrics_count: int = 0


def _make_api_request(
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Make an authenticated request to the Braintrust API."""
    api_key = _get_api_key()
    if not api_key:
        return None

    url = f"{BRAINTRUST_API_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        if method == "GET":
            response = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == "POST":
            response = requests.post(
                url, headers=headers, params=params, json=json_data, timeout=30
            )
        else:
            return None

        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Braintrust API request failed: {e}")
        return None


def _get_project_id() -> Optional[str]:
    """Get the Braintrust project ID for the configured project."""
    result = _make_api_request("/project", params={"org_name": BRAINTRUST_ORG})
    if not result or "objects" not in result:
        return None

    for project in result.get("objects", []):
        if project.get("name") == BRAINTRUST_PROJECT:
            return project.get("id")
    return None


# GitHub repo for the benchmark workflow (used to find latest run ID)
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "HolmesGPT/holmesgpt")
BENCHMARK_WORKFLOW = "eval-benchmarks.yaml"
# Number of recent successful benchmark runs to consider when looking for a
# matching Braintrust experiment. The latest successful workflow run may not
# have a corresponding experiment (e.g. the eval step failed or didn't log),
# so we walk back through recent runs until we find one that does.
BENCHMARK_RUN_LOOKBACK = 20


def _find_recent_workflow_run_ids(
    workflow_file: str, limit: int = BENCHMARK_RUN_LOOKBACK
) -> List[int]:
    """Query GitHub Actions API for recent successful run IDs of a workflow.

    Returns run IDs in newest-first order. Each maps to a Braintrust experiment
    name like '<prefix>{run_id}'.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/runs"
    headers = {"Accept": "application/vnd.github+json"}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    try:
        response = requests.get(
            url,
            params={
                "status": "success",
                "per_page": limit,
            },
            headers=headers,
            timeout=15,
        )
        if response.status_code != 200:
            logging.warning(
                f"GitHub Actions API returned {response.status_code}: {response.text[:200]}"
            )
            return []

        runs = response.json().get("workflow_runs", [])
        return [run["id"] for run in runs if "id" in run]
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        logging.warning(f"GitHub Actions API request failed: {e}")
        return []


def _find_recent_benchmark_run_ids(limit: int = BENCHMARK_RUN_LOOKBACK) -> List[int]:
    return _find_recent_workflow_run_ids(BENCHMARK_WORKFLOW, limit)


def _find_latest_benchmark_experiment(
    project_id: str,
) -> Optional[Dict[str, Any]]:
    """Find the most recent ci-benchmark root experiment.

    Queries GitHub Actions API for recent successful benchmark workflow run IDs,
    then for each run does an exact name lookup in Braintrust via experiment_name
    filter, returning the first match. Walks back through up to
    BENCHMARK_RUN_LOOKBACK runs since the latest successful workflow run may not
    have produced an experiment (e.g., eval step failed before logging).
    """
    run_ids = _find_recent_benchmark_run_ids()
    if not run_ids:
        logging.warning(
            f"No recent successful runs found for workflow '{BENCHMARK_WORKFLOW}' in {GITHUB_REPO}"
        )
        return None

    tried: List[str] = []
    for run_id in run_ids:
        experiment_name = f"{BENCHMARK_EXPERIMENT_PREFIX}{run_id}"
        tried.append(experiment_name)
        result = _make_api_request(
            "/experiment",
            params={
                "project_id": project_id,
                "experiment_name": experiment_name,
            },
        )
        if not result:
            continue

        objects = result.get("objects", [])
        if objects:
            logging.info(f"Found benchmark experiment: {experiment_name}")
            return objects[0]

    logging.warning(
        f"No ci-benchmark experiment found in Braintrust for the last "
        f"{len(run_ids)} successful workflow runs (tried: {', '.join(tried[:5])}"
        f"{'...' if len(tried) > 5 else ''})"
    )
    return None


def _find_latest_master_experiment(
    project_id: str,
) -> Optional[Dict[str, Any]]:
    """Find the most recent master-* experiment from the post-merge workflow.

    Mirrors _find_latest_benchmark_experiment but against eval-master.yaml, which
    runs the regression set on every push to master. Falls back to None if the
    workflow has never run successfully (e.g. before the workflow was deployed).
    """
    run_ids = _find_recent_workflow_run_ids(MASTER_WORKFLOW)
    if not run_ids:
        logging.info(
            f"No recent successful runs for workflow '{MASTER_WORKFLOW}' in {GITHUB_REPO}"
        )
        return None

    tried: List[str] = []
    for run_id in run_ids:
        experiment_name = f"{MASTER_EXPERIMENT_PREFIX}{run_id}"
        tried.append(experiment_name)
        result = _make_api_request(
            "/experiment",
            params={
                "project_id": project_id,
                "experiment_name": experiment_name,
            },
        )
        if not result:
            continue
        objects = result.get("objects", [])
        if objects:
            logging.info(f"Found master experiment: {experiment_name}")
            return objects[0]

    logging.warning(
        f"No master experiment found in Braintrust for the last "
        f"{len(run_ids)} successful master runs (tried: {', '.join(tried[:5])}"
        f"{'...' if len(tried) > 5 else ''})"
    )
    return None


def _fetch_all_eval_spans(experiment_id: str) -> List[Dict[str, Any]]:
    """Fetch all eval-type spans from an experiment, handling pagination."""
    all_eval_spans: List[Dict[str, Any]] = []
    cursor = None

    # Braintrust returns all span types (eval, llm, tool, score, task) interleaved;
    # we filter for eval client-side. A weekly benchmark across ~7 models generates
    # 4000+ total events, so use a large page size to avoid stopping before all
    # eval spans have been seen (was limit=100 -> truncated baseline at ~2000 events).
    for _ in range(50):  # Safety limit on pagination
        body: Dict[str, Any] = {"limit": 1000}
        if cursor:
            body["cursor"] = cursor

        result = _make_api_request(
            f"/experiment/{experiment_id}/fetch",
            method="POST",
            json_data=body,
        )
        if not result:
            break

        events = result.get("events", [])
        if not events:
            break

        # Filter for eval spans client-side (more reliable than server-side filter)
        for event in events:
            span_attrs = event.get("span_attributes") or {}
            if span_attrs.get("type") == "eval":
                all_eval_spans.append(event)

        cursor = result.get("cursor")
        if not cursor:
            break

    return all_eval_spans


def _extract_metrics(span: Dict[str, Any]) -> Optional[BenchmarkMetrics]:
    """Extract metrics from an eval span."""
    metadata = span.get("metadata") or {}
    scores = span.get("scores") or {}
    metrics = span.get("metrics") or {}

    test_id = metadata.get("eval_id") or metadata.get("test_id", "")
    model = metadata.get("model", "")

    if not test_id or not model:
        return None

    duration = metadata.get("holmes_duration")
    tool_calls = metadata.get("tool_call_count")
    num_llm_calls = metadata.get("num_llm_calls")
    correctness = scores.get("correctness")
    passed = int(correctness) == 1 if correctness is not None else False

    # Cost from metrics (logged by Braintrust SDK) or metadata (logged by us)
    cost = metrics.get("cost") or metadata.get("cost")

    # Token data from metadata (logged by us via eval span)
    total_tokens = metadata.get("total_tokens")
    cached_tokens = metadata.get("cached_tokens")

    return BenchmarkMetrics(
        test_id=test_id,
        model=model,
        passed=passed,
        duration=float(duration) if duration is not None else None,
        cost=float(cost) if cost is not None else None,
        tool_call_count=int(tool_calls) if tool_calls is not None else None,
        num_llm_calls=int(num_llm_calls) if num_llm_calls is not None else None,
        total_tokens=int(total_tokens) if total_tokens is not None else None,
        cached_tokens=int(cached_tokens) if cached_tokens is not None else None,
    )


def _load_baseline_from_experiment(
    finder, filter_description: str, missing_status: str
) -> Tuple[Dict[str, BenchmarkMetrics], HistoricalComparisonDetails]:
    """Shared loader: resolve project, find experiment via `finder(project_id)`, extract metrics."""
    details = HistoricalComparisonDetails(filter_description=filter_description)
    try:
        api_key = _get_api_key()
        if not api_key:
            details.status = "No Braintrust API key (BRAINTRUST_API_KEY or BRAINTRUST_SERVICE_TOKEN)"
            return {}, details

        project_id = _get_project_id()
        if not project_id:
            details.status = f"Braintrust project '{BRAINTRUST_PROJECT}' not found"
            return {}, details
        details.project_id = project_id

        exp = finder(project_id)
        if not exp:
            details.status = missing_status
            return {}, details

        exp_metadata = exp.get("metadata") or {}
        exp_info = ExperimentInfo(
            id=exp.get("id", ""),
            name=exp.get("name", ""),
            branch=exp_metadata.get("branch", "unknown"),
            created=exp.get("created"),
        )
        details.experiments.append(exp_info)
        logging.info(f"Using baseline experiment: {exp_info.name} (created {exp_info.created})")

        eval_spans = _fetch_all_eval_spans(exp["id"])
        if not eval_spans:
            details.status = f"No eval spans found in experiment '{exp_info.name}'"
            return {}, details

        metrics_map: Dict[str, BenchmarkMetrics] = {}
        for span in eval_spans:
            m = _extract_metrics(span)
            if m is None:
                continue
            metrics_map[f"{m.test_id}:{m.model}"] = m

        details.metrics_count = len(metrics_map)
        logging.info(
            f"Loaded {len(metrics_map)} test/model results from '{exp_info.name}'"
        )
        return metrics_map, details

    except Exception as e:
        tb = traceback.format_exc()
        logging.error(f"Error fetching baseline: {e}\n{tb}")
        details.status = f"Error: {e}"
        details.errors.append(f"{e}\n{tb}")
        return {}, details


def get_benchmark_baseline() -> (
    Tuple[Dict[str, BenchmarkMetrics], HistoricalComparisonDetails]
):
    """Fetch metrics from the latest weekly ci-benchmark experiment."""
    return _load_baseline_from_experiment(
        finder=_find_latest_benchmark_experiment,
        filter_description="latest ci-benchmark experiment on master",
        missing_status="No ci-benchmark experiments found",
    )


def get_master_baseline() -> (
    Tuple[Dict[str, BenchmarkMetrics], HistoricalComparisonDetails]
):
    """Fetch metrics from the latest master-* experiment (post-merge eval).

    Populated by .github/workflows/eval-master.yaml, which runs the regression
    set on every push to master and logs as `master-<run_id>`. Returns an empty
    map with a populated `details.status` if no such experiment exists yet
    (e.g. before the workflow is deployed).
    """
    return _load_baseline_from_experiment(
        finder=_find_latest_master_experiment,
        filter_description="latest master-* experiment (post-merge regression eval)",
        missing_status="No master-* experiments found (eval-master.yaml may not have run yet)",
    )


def compare_with_benchmark(
    current_results: List[Dict[str, Any]],
    benchmark: Dict[str, BenchmarkMetrics],
) -> Dict[str, HistoricalComparison]:
    """Compare current test results with benchmark baseline.

    Args:
        current_results: List of current test result dictionaries
        benchmark: Benchmark metrics from get_benchmark_baseline()

    Returns:
        Dict mapping "test_id:model" to HistoricalComparison
    """
    comparisons: Dict[str, HistoricalComparison] = {}
    matched = 0
    current_keys_sample: List[str] = []
    skipped_no_model_or_id = 0

    for result in current_results:
        if result is None:
            continue
        test_id = result.get("test_case_name", result.get("clean_test_case_id", ""))
        model = result.get("model", "")

        if not test_id or not model:
            skipped_no_model_or_id += 1
            continue

        key = f"{test_id}:{model}"
        if len(current_keys_sample) < 5:
            current_keys_sample.append(key)
        baseline = benchmark.get(key)
        if baseline:
            matched += 1

        comparison = HistoricalComparison(
            test_id=test_id,
            model=model,
            current_duration=result.get("holmes_duration"),
            current_cost=result.get("cost"),
            current_passed=result.get("passed"),
        )

        if baseline:
            comparison.benchmark_passed = baseline.passed
            comparison.historical_avg_duration = baseline.duration
            comparison.historical_avg_cost = baseline.cost

            # Calculate percentage differences (only for passing tests)
            if comparison.current_duration and baseline.duration:
                comparison.duration_diff_pct = (
                    (comparison.current_duration - baseline.duration)
                    / baseline.duration
                    * 100
                )

            if comparison.current_cost and baseline.cost:
                comparison.cost_diff_pct = (
                    (comparison.current_cost - baseline.cost) / baseline.cost * 100
                )

        comparisons[key] = comparison

    if benchmark and current_keys_sample and matched == 0:
        baseline_sample = list(benchmark.keys())[:5]
        logging.warning(
            "compare_with_benchmark: 0 of %d current results matched any of %d "
            "baseline metrics (skipped %d entries missing test_id/model). "
            "Sample current keys=%s, sample baseline keys=%s. "
            "Check that test_case_name and model names use the same format in both runs.",
            len(comparisons),
            len(benchmark),
            skipped_no_model_or_id,
            current_keys_sample,
            baseline_sample,
        )
    elif benchmark:
        logging.info(
            "compare_with_benchmark: matched %d of %d current results against %d baseline metrics (skipped %d missing test_id/model)",
            matched,
            len(comparisons),
            len(benchmark),
            skipped_no_model_or_id,
        )

    return comparisons
